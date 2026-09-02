"""Derive countable facts from what ingest already stored.

One ``INSERT ... SELECT`` over ``documents``: the rows are already in this
database, so pulling them into Python to push them back would be slower and
would invent a failure mode (a half-written batch) that a single statement does
not have.

Idempotent by design, because every sync re-lists every document. The partial
unique indexes on ``activity_facts`` are the guard, and an edit *moves* the
existing fact rather than adding a second one -- a page edited five times is
one page.
"""

from __future__ import annotations

import logging

from ..core.exceptions import ProviderError
from ..db.connection import get_connection

logger = logging.getLogger(__name__)

#: Providers that have ``documents`` rows to derive facts from.
#:
#: GitHub is absent on purpose: it embeds nothing, so it has no documents and
#: no chunks. Reading them for GitHub would return zero forever, which on a
#: chart is indistinguishable from "no activity" -- so ``record_document_facts``
#: raises instead of quietly succeeding. GitHub's facts come from
#: ``github_facts.py`` and its own live reads.
DOCUMENT_PROVIDERS = ("notion", "google", "slack", "linear")

#: The one ``activity_facts.kind`` this module writes. Notion pages, Drive
#: files, Slack threads and Linear issues all arrive as documents, so they
#: share a kind and are separated by ``provider`` -- which is why every query
#: in ``store.py`` filters on both.
_KIND = "doc_changed"


def record_document_facts(
    org_id: str, *, provider: str, workspace_id: str | None
) -> int:
    """Record one ``doc_changed`` fact per indexed document in this scope.

    ``source_last_modified`` is the fact's date. Documents without one are
    skipped rather than stamped ``now()``: stamping would invent an edit that
    never happened and pile every undated document onto today's bar.

    Returns the number of rows inserted or moved. Raises ``ValueError`` for a
    provider that has no documents -- see ``DOCUMENT_PROVIDERS``.
    """
    if provider not in DOCUMENT_PROVIDERS:
        raise ValueError(
            f"{provider!r} has no indexed documents to count; "
            f"expected one of {DOCUMENT_PROVIDERS}"
        )

    # The conflict target must match one of the two PARTIAL unique indexes, and
    # which one applies depends on the scope -- Postgres treats NULLs as
    # distinct in a plain UNIQUE, which is why they are partial in the first
    # place. DO UPDATE, not DO NOTHING: an edit moves the fact's date forward.
    if workspace_id is None:
        conflict = """
            ON CONFLICT (org_id, provider, kind, external_id)
                WHERE workspace_id IS NULL AND external_id IS NOT NULL
        """
        scope = "AND d.workspace_id IS NULL"
    else:
        conflict = """
            ON CONFLICT (org_id, workspace_id, provider, kind, external_id)
                WHERE workspace_id IS NOT NULL AND external_id IS NOT NULL
        """
        scope = "AND d.workspace_id = %(workspace_id)s"

    sql = f"""
        INSERT INTO activity_facts
            (org_id, workspace_id, provider, kind, subject, occurred_at,
             url, external_id)
        SELECT d.org_id, d.workspace_id, d.source_provider, %(kind)s, d.title,
               d.source_last_modified, d.source_uri, d.source_external_id
          FROM documents d
         WHERE d.org_id = %(org_id)s
           AND d.source_provider = %(provider)s
           AND d.source_last_modified IS NOT NULL
           AND d.source_external_id IS NOT NULL
           {scope}
        {conflict}
        DO UPDATE SET occurred_at = EXCLUDED.occurred_at,
                      subject     = EXCLUDED.subject,
                      url         = EXCLUDED.url
    """

    params = {
        "org_id": org_id,
        "provider": provider,
        "workspace_id": workspace_id,
        "kind": _KIND,
    }

    try:
        with get_connection() as conn:
            written = conn.execute(sql, params).rowcount
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - re-raised as our own type
        raise ProviderError(
            f"insights: recording {provider} document facts failed", cause=exc
        ) from exc

    logger.info(
        "insights: recorded %s %s document facts for org %s (workspace=%s)",
        written, provider, org_id, workspace_id,
    )
    return written
