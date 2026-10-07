"""Run BACKGROUND dataset adapters on the tick, never inside ingestion.

Ingestion asks each background adapter only its cheap ``wants`` question and,
on yes, ``enqueue``s the document with a bounded copy of its text. The tick
calls ``run_pending``, which claims a small batch, waits for a background LLM
slot (``llm.pacing`` keeps the reserve free for live questions), runs the
adapter and stores what it found. So an ingest finishes exactly as fast as it
did before, and the figures arrive a few minutes later.

Bounded at every step: ``text_batch`` documents per tick, ``text_max_chars``
of each, ``text_max_attempts`` tries before a document is given up on (so one
page that always fails cannot hold the queue), and a claim expires after
``CLAIM_TTL_MINUTES`` so a tick that died mid-batch does not strand its rows.
"""

from __future__ import annotations

import logging

from ..config.settings import DocTablesSettings
from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from .base import DocumentText

logger = logging.getLogger(__name__)

CLAIM_TTL_MINUTES = 10


def enqueue(document_id: str, *, org_id: str, workspace_id: str | None,
            origin: str, text: DocumentText, max_chars: int | None = None) -> None:
    """Flag a document for a background adapter. Raises; ingestion catches."""
    max_chars = max_chars or DocTablesSettings.from_env().text_max_chars
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO doc_text_queue
                (document_id, org_id, workspace_id, origin, external_id, title, text)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE
               SET text = EXCLUDED.text, title = EXCLUDED.title, status = 'pending',
                   attempts = 0, claimed_at = NULL, updated_at = now()
            """,
            (document_id, org_id, workspace_id, origin, text.external_id,
             text.title[:300], (text.content or "")[: max_chars + 2000]),
        )
        conn.commit()


def _claim(limit: int) -> list[tuple]:
    # A CTE, never `WHERE ... IN (SELECT ... SKIP LOCKED LIMIT n)`: the latter
    # does not bound an UPDATE (CLAUDE.md §5).
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            WITH picked AS (
                SELECT document_id FROM doc_text_queue
                 WHERE status = 'pending'
                   AND (claimed_at IS NULL
                        OR claimed_at < now() - interval '{CLAIM_TTL_MINUTES} minutes')
                 ORDER BY updated_at
                 LIMIT %s
                 FOR UPDATE SKIP LOCKED
            )
            UPDATE doc_text_queue q
               SET claimed_at = now(), attempts = q.attempts + 1, updated_at = now()
              FROM picked
             WHERE q.document_id = picked.document_id
            RETURNING q.document_id, q.org_id, q.workspace_id, q.origin,
                      q.external_id, q.title, q.text, q.attempts
            """,
            (limit,),
        ).fetchall()
        conn.commit()
    return rows


def _finish(document_id, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE doc_text_queue SET status = %s, text = NULL, claimed_at = NULL, "
            "updated_at = now() WHERE document_id = %s",
            (status, document_id),
        )
        conn.commit()


def _release(document_id, *, refund: bool) -> None:
    """Back to pending. ``refund`` gives the attempt back when the failure was
    ours (no LLM slot free), not the document's."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE doc_text_queue SET claimed_at = NULL, "
            "attempts = attempts - %s, updated_at = now() WHERE document_id = %s",
            (1 if refund else 0, document_id),
        )
        conn.commit()


def run_pending(settings: DocTablesSettings | None = None, *, llm=None,
                wait_for_slot=None) -> int:
    """Process one batch. Returns how many documents finished. Never raises.

    A no-op when no background adapter is switched on.
    """
    settings = settings or DocTablesSettings.from_env()
    try:
        from .factory import build_dataset_adapters
        from .store import replace_document_tables

        if llm is None and settings.text_enabled:
            from ..llm.factory import build_aux_llm_provider

            llm = build_aux_llm_provider()
        adapters = {a.origin: a for a in build_dataset_adapters(settings, llm=llm)
                    if a.background}
        if not adapters:
            return 0
        if wait_for_slot is None:
            from ..llm.pacing import wait_for_background_slot as wait_for_slot

        finished = 0
        for (document_id, org_id, workspace_id, origin, external_id, title, text,
             attempts) in _claim(settings.text_batch):
            adapter = adapters.get(origin)
            if adapter is None or not text:
                _finish(document_id, "failed")
                continue
            if not wait_for_slot():
                # The background budget is spent by live traffic: try later,
                # and do not count it against the document.
                _release(document_id, refund=True)
                continue
            try:
                tables = adapter.extract(DocumentText(
                    external_id=external_id, title=title, content=text,
                ))
            except ProviderError:
                logger.warning("doctables: %s failed on %s (attempt %s)",
                               origin, external_id, attempts, exc_info=True)
                if attempts >= settings.text_max_attempts:
                    _finish(document_id, "failed")
                else:
                    _release(document_id, refund=False)
                continue
            if tables:
                replace_document_tables(
                    str(document_id), org_id=str(org_id),
                    workspace_id=str(workspace_id) if workspace_id else None,
                    tables=tables, origin=origin,
                )
            _finish(document_id, "done")
            finished += 1
        if finished:
            logger.info("doctables: read figures from %s document(s)", finished)
        return finished
    except Exception:  # noqa: BLE001 - a later chart, never a failed tick
        logger.exception("doctables: background batch failed")
        return 0
