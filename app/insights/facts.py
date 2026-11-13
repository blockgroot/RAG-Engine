"""Derive countable facts from what ingest already stored.

One ``INSERT ... SELECT`` over ``documents`` (no half-written batch).
Idempotent: the partial unique indexes guard it, and an edit MOVES the fact,
so a page edited five times is one page.
"""

from __future__ import annotations

import logging

from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from ..sources.factory import INDEXED_PROVIDERS

logger = logging.getLogger(__name__)

#: Providers with ``documents`` rows. GitHub is absent (it embeds nothing), and
#: ``record_document_facts`` raises for it rather than return a silent zero.
#: Imported, not restated: a second copy let a disconnected Linear keep its rows.
DOCUMENT_PROVIDERS = INDEXED_PROVIDERS

#: The one kind this module writes, shared by every document provider, so
#: every query in ``store.py`` filters on kind AND provider.
_KIND = "doc_changed"


def record_form_facts_if_enabled(
    org_id: str, *, workspace_id: str | None, token: str | None = None
) -> int:
    """Classify new form responses, if Forms reading is switched on.

    Rides the Google ingest job (the tick would find it not-due). Returns 0
    when disabled: a configuration state, not an error.
    """
    from ..config.settings import GoogleSettings

    if not GoogleSettings.from_env().forms_enabled:
        return 0

    try:
        from ..auth.credentials import get_live_connection_token
        from ..sources.google_forms import GoogleFormsReader

        from .sentiment import record_form_sentiment

        # The admin's ALLOW-LIST (`connection_ops.set_google_form_ids`). Empty
        # reads nothing: a Drive token sees every form, including personal ones.
        from ..auth import get_connection_config

        config = get_connection_config(org_id, "google", workspace_id=workspace_id) or {}
        form_ids = list(config.get("form_ids") or [])
        if not form_ids:
            return 0

        resolved = token or get_live_connection_token(
            org_id, "google", workspace_id
        )
        return record_form_sentiment(
            org_id, workspace_id=workspace_id,
            reader=GoogleFormsReader(resolved),
            form_ids=form_ids,
        )
    except Exception:  # noqa: BLE001 - a missing chart, never a failed job
        logger.warning(
            "insights: could not classify form responses for org %s", org_id,
            exc_info=True,
        )
        return 0


def record_document_facts(
    org_id: str, *, provider: str, workspace_id: str | None
) -> int:
    """Record one ``doc_changed`` fact per indexed document in this scope.

    Dated by ``source_last_modified``; undated documents are skipped, never
    stamped ``now()``. Returns rows inserted or moved. Raises ``ValueError``
    for a provider without documents (``DOCUMENT_PROVIDERS``).
    """
    if provider not in DOCUMENT_PROVIDERS:
        raise ValueError(
            f"{provider!r} has no indexed documents to count; "
            f"expected one of {DOCUMENT_PROVIDERS}"
        )

    # Conflict target must match the scope's PARTIAL unique index (NULLs are
    # distinct in a plain UNIQUE). DO UPDATE: an edit moves the date forward.
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

    # `subject`: the title for Notion/Drive, the CHANNEL for Slack (taken from
    # the "#general: snippet" title; one bar per thread would be useless).
    subject = (
        "split_part(d.title, ':', 1)" if provider == "slack" else "d.title"
    )

    sql = f"""
        INSERT INTO activity_facts
            (org_id, workspace_id, provider, kind, actor, subject, occurred_at,
             url, external_id, actor_key)
        SELECT d.org_id, d.workspace_id, d.source_provider, %(kind)s,
               d.source_last_editor, {subject},
               d.source_last_modified, d.source_uri, d.source_external_id,
               d.source_editor_key
          FROM documents d
         WHERE d.org_id = %(org_id)s
           AND d.source_provider = %(provider)s
           AND d.source_last_modified IS NOT NULL
           AND d.source_external_id IS NOT NULL
           {scope}
        {conflict}
        DO UPDATE SET occurred_at = EXCLUDED.occurred_at,
                      subject     = EXCLUDED.subject,
                      url         = EXCLUDED.url,
                      actor       = EXCLUDED.actor,
                      actor_key   = EXCLUDED.actor_key
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


#: Bound so a tick cannot walk every tenant in one request if this table
#: ever grows. 200 scopes is far above today's org count; raise it if a
#: tick log shows we hit the cap.
_BACKFILL_SCOPE_CAP = 200


def backfill_all_document_facts() -> int:
    """Copy existing documents into ``activity_facts`` without waiting for a sync.

    For tenants with documents but no facts (connected before charts, or a
    failing tick). Same ``INSERT ... SELECT`` for every (org, provider, space)
    with dated documents. Idempotent; missing authors stay NULL.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT d.org_id::text, d.source_provider, d.workspace_id::text
                  FROM documents d
                 WHERE d.source_provider = ANY(%s)
                   AND d.source_last_modified IS NOT NULL
                   AND d.source_external_id IS NOT NULL
                   AND NOT EXISTS (
                       SELECT 1 FROM activity_facts f
                        WHERE f.org_id = d.org_id
                          AND f.provider = d.source_provider
                          AND f.kind = 'doc_changed'
                          AND f.workspace_id IS NOT DISTINCT FROM d.workspace_id
                   )
                 LIMIT %s
                """,
                (list(DOCUMENT_PROVIDERS), _BACKFILL_SCOPE_CAP),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(
            "insights: listing document scopes for fact backfill failed",
            cause=exc,
        ) from exc

    written = 0
    for org_id, provider, workspace_id in rows:
        try:
            written += record_document_facts(
                org_id, provider=provider, workspace_id=workspace_id,
            )
        except Exception:  # noqa: BLE001 - one org must not abort the rest
            logger.warning(
                "insights: backfill of %s facts for org %s failed",
                provider, org_id, exc_info=True,
            )
    if written:
        logger.info("insights: backfilled %s document facts across %s scopes",
                    written, len(rows))
    return written
