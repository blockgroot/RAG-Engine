"""Run one scheduler: fetch activity → write the report → email it.

Kept separate from ``worker.py`` (which owns claiming and status
bookkeeping) so this function is a pure "do the work" unit with no queue
knowledge — that is what makes it testable without a database, and what lets
the worker decide independently whether a raised exception means retry or
retire.

Failure policy, which is the interesting part:

- A fetch or LLM failure **raises**, so the worker records it and retries.
  There is no report to deliver, so a silent success would leave the user
  waiting a full cycle for a mail that is never coming.
- An email failure is **swallowed** by ``..._safe``. The expensive work is
  already done; retrying the whole run on a transient mail error risks
  delivering the same report twice, which is worse than dropping one.

The report is **saved before the mail is attempted**, and the mail is only a
notification with a link into the app. That ordering is what makes the
swallowed email failure cheap: the run's output is already readable, so what
a failed send costs is the nudge, not the report.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..auth.email import send_scheduler_report_email_safe
from ..auth.users import get_user
from ..config.settings import ApiSettings
from ..core.exceptions import ConfigurationError
from ..llm import build_llm_provider
from ..llm.base import LLMProvider
from ..workspaces.store import get_workspace_name
from . import reports
from .activity import fetch_activity
from .prompts import NO_ACTIVITY_NOTE, build_scheduler_report_prompt
from .store import Scheduler

logger = logging.getLogger(__name__)

# Window used for a scheduler's very first run, when there is no last_run_at
# to measure from. Matched to the cadence so the first report is not oddly
# thin (weekly) or unboundedly deep (monthly on a busy service).
_FIRST_WINDOW = {"weekly": timedelta(days=7), "monthly": timedelta(days=30)}
_DEFAULT_FIRST_WINDOW = timedelta(days=7)


def window_start(scheduler: Scheduler) -> datetime:
    """When this run's activity window begins.

    ``last_run_at`` if the scheduler has run before — so consecutive reports
    tile the timeline with no gap and no overlap. Note the queue deliberately
    does *not* advance ``last_run_at`` on failure, which is what makes a
    retried run still cover everything since the last **delivered** report
    rather than silently dropping the activity in between.
    """
    if scheduler.last_run_at:
        return scheduler.last_run_at
    window = _FIRST_WINDOW.get(scheduler.frequency, _DEFAULT_FIRST_WINDOW)
    return datetime.now(timezone.utc) - window


def report_link(report_id: str, settings: ApiSettings | None = None) -> str:
    """Where a reader opens this report.

    Built against the FRONTEND origin, never the API host: the session cookie
    is ``SameSite=Lax`` and would not travel to a different origin, so a link
    to the API would land the reader on a login page (CLAUDE.md §5).
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
    """Produce and email one report. Returns the report text (for tests/logs).

    Raises on a fetch or LLM failure so the caller can record it; never
    raises for a mail problem (see the module docstring).
    """
    user = get_user(scheduler.user_id)
    if user is None or not user.email:
        # The owning user was deleted between claim and run. Not retryable —
        # raise so the worker records it and the attempts cap retires it.
        raise ConfigurationError(
            f"Scheduler {scheduler.id} has no deliverable owner (user "
            f"{scheduler.user_id} is gone)."
        )

    window_end = datetime.now(timezone.utc)
    since = window_start(scheduler)
    # The row's own scope wins; the argument is a test/manual override only.
    scope_id = workspace_id or scheduler.workspace_id
    # Snapshotted into the report so an archived one keeps reading correctly
    # after the space is renamed — or deleted.
    space_name = (
        get_workspace_name(scheduler.org_id, scope_id) if scope_id else None
    )
    # A scheduler created against a sub-workspace's connection must keep
    # reading that connection, never silently fall back to the org-wide one —
    # a space sees ONLY its own rows (CLAUDE.md §3).
    digest = fetch_activity(
        scheduler.provider, scheduler.org_id, since, workspace_id=scope_id
    )

    if not digest:
        # Skip the LLM entirely — there is nothing to summarise, and a model
        # handed an empty context is exactly where invention happens. Same
        # instinct as the RAG confidence gate refusing before generating.
        # Note `digest` is falsy on *items*, not notes: a run that only
        # learned "channels checked: …" still has nothing to report on.
        report = NO_ACTIVITY_NOTE
    else:
        # The MAIN provider, not the aux one. A report is the product's most
        # visible generated artifact; the aux model exists for cheap
        # classification stages (the setup chat's tool call), and pointing a
        # report at it produced one-line summaries of substantial content.
        # This also gives the deploy a real knob: LLM_AUX_MODEL can stay on a
        # lite model while LLM_MODEL carries the reports.
        llm = llm or build_llm_provider()
        prompt = build_scheduler_report_prompt(
            scheduler.prompt, digest, scheduler.provider
        )
        report = llm.generate(prompt).strip()

    # Items and notes are stored as structured data, not folded into the
    # model's prose: the report page renders each source link itself so a link
    # can never be invented or dropped, and the coverage notes reach the reader
    # even when the model declines to mention them.
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
        notes=list(digest.notes),
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
        # Stamped only on an accepted send, so "was this actually emailed?"
        # stays answerable rather than assumed — the report is readable either
        # way, which is the point of saving first.
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
