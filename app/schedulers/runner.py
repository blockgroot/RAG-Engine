"""Run one scheduler: fetch activity, write the report, email it.

No queue knowledge here; ``worker.py`` decides retry or retire.

- A fetch or LLM failure raises, so the worker retries.
- An email failure is swallowed: retrying could deliver the report twice.
  The report is saved before the mail, so a failed send loses only the nudge.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..auth.email import send_scheduler_report_email_safe
from ..auth.users import get_user
from ..config.settings import ApiSettings
from ..core.exceptions import ConfigurationError
from ..llm import build_aux_llm_provider
from ..llm.routed import default_model_only
from ..llm.base import LLMProvider
from ..sources.google_groups import viewer_for_person
from ..workspaces.store import get_workspace_name
from . import reports
from .activity import fetch_activity
from .prompts import NO_ACTIVITY_NOTE, build_scheduler_report_prompt
from .store import Scheduler

logger = logging.getLogger(__name__)

# First run is a catch-up, not one cadence: a cadence-sized first window
# reported "nothing happened" on busy repos. Bounded: every fetcher caps its
# item count newest-first. Later runs start at the previous one's end.
FIRST_RUN_LOOKBACK = timedelta(days=90)

#: Says why the first window is wider than the cadence.
FIRST_RUN_NOTE = (
    "First report for this schedule, so it covers the last 90 days rather than "
    "one period. The next one starts where this one ends."
)


def window_start(scheduler: Scheduler) -> datetime:
    """When this run's window begins: ``last_run_at``, so reports tile with no
    gap or overlap. The queue does not advance it on failure, so a retry still
    covers everything since the last delivered report.
    """
    if scheduler.last_run_at:
        return scheduler.last_run_at
    return datetime.now(timezone.utc) - FIRST_RUN_LOOKBACK


def report_link(report_id: str, settings: ApiSettings | None = None) -> str:
    """Where a reader opens this report: the frontend origin, never the API
    host, whose link would miss the ``SameSite=Lax`` cookie and hit login.
    """
    settings = settings or ApiSettings.from_env()
    base = (settings.frontend_url or "").rstrip("/")
    return f"{base}/schedulers/reports/{report_id}"


def run_scheduler_once(
    scheduler: Scheduler,
    *,
    llm: LLMProvider | None = None,
    workspace_id: str | None = None,
) -> str:
    """Produce and email one report; returns its text. Raises on fetch or LLM
    failure, never for mail.
    """
    user = get_user(scheduler.user_id)
    if user is None or not user.email:
        # Owner deleted: raise so the attempts cap retires it.
        raise ConfigurationError(
            f"Scheduler {scheduler.id} has no deliverable owner (user "
            f"{scheduler.user_id} is gone)."
        )

    window_end = datetime.now(timezone.utc)
    since = window_start(scheduler)
    # The row's own scope wins; the argument is a test/manual override only.
    scope_id = workspace_id or scheduler.workspace_id
    # Snapshotted so an archived report survives a space rename or delete.
    space_name = (
        get_workspace_name(scheduler.org_id, scope_id) if scope_id else None
    )
    # Fire-and-forget sync: waiting would hold the claimed row through an
    # ingest. This report reads what is indexed now; the sync keeps the next
    # one current. GitHub reads live.
    if scheduler.provider != "github":
        try:
            from ..jobs.autosync import request_sync

            request_sync(scheduler.org_id, scheduler.provider, scope_id)
        except Exception:  # noqa: BLE001 - a report must not fail over this
            logger.debug("Could not request a pre-report sync", exc_info=True)

    # A space's scheduler reads only that space's rows, never org-wide ones
    # (CLAUDE.md §3), and as its creator: document access applies, or a Drive
    # digest would mail files they were never shared on.
    digest = fetch_activity(
        scheduler.provider,
        scheduler.org_id,
        since,
        workspace_id=scope_id,
        viewer=viewer_for_person(scheduler.org_id, user.email),
    )
    notes = list(digest.notes)
    if scheduler.last_run_at is None:
        notes.append(FIRST_RUN_NOTE)

    if not digest:
        # No LLM on an empty context: that is where invention happens.
        # `digest` is falsy on items, not notes.
        report = NO_ACTIVITY_NOTE
    else:
        # Background work runs on the aux model. Keep it a full model: a lite
        # one wrote one-line summaries of substantial content.
        llm = llm or build_aux_llm_provider()
        prompt = build_scheduler_report_prompt(
            scheduler.prompt, digest, scheduler.provider
        )
        # Unattended: always the configured model, never a picked one.
        with default_model_only():
            report = llm.generate(prompt).strip()

    # Items and notes stored as data, so links are never invented or dropped
    # and notes reach the reader even if the model omits them.
    saved = reports.save_report(
        scheduler_id=scheduler.id,
        org_id=scheduler.org_id,
        user_id=scheduler.user_id,
        provider=scheduler.provider,
        frequency=scheduler.frequency,
        prompt=scheduler.prompt,
        space_name=space_name,
        report_text=report,
        items=[
            {"summary": i.summary, "url": i.url, "meta": i.meta}
            for i in digest.items
        ],
        notes=notes,
        window_start=since,
        window_end=window_end,
    )

    delivered = send_scheduler_report_email_safe(
        user.email,
        provider=scheduler.provider,
        frequency=scheduler.frequency,
        scheduler_prompt=scheduler.prompt,
        link=report_link(saved.id),
        item_count=len(digest.items),
        space_name=space_name,
    )
    if delivered:
        # Stamped only on an accepted send.
        reports.mark_delivered(saved.id, user.email)

    logger.info(
        "Scheduler %s: %s report saved as %s (%s item(s), %s chars); email %s",
        scheduler.id,
        scheduler.provider,
        saved.id,
        len(digest.items),
        len(digest.text),
        "sent" if delivered else "FAILED",
    )
    return report
