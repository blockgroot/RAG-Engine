"""Delete rows nobody can use any more, so tables do not grow forever.

Chats, attachments, live-read audit rows and Slack receipts have their own
purges; this covers the rest. Each rule is one bounded DELETE (``BATCH`` rows
a pass, so a first run on a big table never holds a long lock), run by
``worker.run_upkeep``. A rule that fails is logged and the others still run.

What is kept on purpose: documents, chunks, facts, tables and the graph (the
company's data, removed by sync), people and connections, the latest sync job
of each connection (Setup shows it) and the latest report of each schedule
(the next one covers what changed since it).
"""

from __future__ import annotations

import logging

from ..db.connection import get_connection

logger = logging.getLogger(__name__)

#: Rows deleted per rule per pass; upkeep runs hourly, so a backlog drains.
BATCH = 5000

#: ``(table, WHERE)``: our own SQL only, no values from outside.
RULES: tuple[tuple[str, str], ...] = (
    # Single-use tokens: refused once expired; a day's grace for support questions.
    ("magic_link_tokens", "expires_at < now() - interval '1 day'"),
    ("email_change_requests", "expires_at < now() - interval '1 day'"),
    ("oauth_states", "expires_at < now() - interval '1 day'"),
    # Holds an encrypted GitHub token: gone as soon as it can no longer be used.
    ("github_install_pending", "expires_at < now() - interval '1 day'"),
    # Agent mode: a run's conversation lives 15 minutes; its drafts and what it read go after a day.
    ("act_runs", "expires_at < now() - interval '1 day'"),
    # Cards never sent, then the record of what was sent.
    ("actions", "(status IN ('rejected', 'expired') OR (status = 'proposed' AND expires_at < now())) "
                "AND created_at < now() - interval '30 days'"),
    ("actions", "status IN ('succeeded', 'failed', 'unknown') AND created_at < now() - interval '90 days'"),
    # Finished sync jobs, keeping each connection's latest.
    ("ingestion_jobs", "status IN ('succeeded', 'failed') AND created_at < now() - interval '30 days' "
                       "AND id <> (SELECT j.id FROM ingestion_jobs j WHERE j.connection_id = ingestion_jobs.connection_id "
                       "ORDER BY j.created_at DESC LIMIT 1)"),
    # Reports: the page shows recent ones; each schedule keeps its latest.
    ("scheduler_reports", "created_at < now() - interval '90 days' AND scheduler_id IS NOT NULL "
                          "AND id <> (SELECT r.id FROM scheduler_reports r WHERE r.scheduler_id = scheduler_reports.scheduler_id "
                          "ORDER BY r.created_at DESC LIMIT 1)"),
    ("scheduler_reports", "created_at < now() - interval '90 days' AND scheduler_id IS NULL"),
    # Questions people asked: gaps look back 14 days, so 90 is plenty.
    ("feedback_and_gaps", "created_at < now() - interval '90 days'"),
    ("gap_topics", "created_at < now() - interval '90 days' AND NOT EXISTS "
                   "(SELECT 1 FROM feedback_and_gaps f WHERE f.topic_id = gap_topics.id)"),
    # A refused sign-up: the email of someone who is not a customer.
    ("org_signup_requests", "status = 'rejected' AND created_at < now() - interval '90 days'"),
)


def purge_expired() -> dict[str, int]:
    """Run every rule once. Never raises. Returns rows deleted per table."""
    deleted: dict[str, int] = {}
    for table, where in RULES:
        try:
            with get_connection() as conn:
                cur = conn.execute(
                    f"WITH doomed AS (SELECT ctid FROM {table} WHERE {where} LIMIT {BATCH}) "
                    f"DELETE FROM {table} WHERE ctid IN (SELECT ctid FROM doomed)"
                )
            deleted[table] = deleted.get(table, 0) + (cur.rowcount or 0)
        except Exception:  # noqa: BLE001 - a longer table, never a stopped upkeep
            logger.exception("Retention: %s purge failed", table)
    return deleted
