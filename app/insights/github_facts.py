"""GitHub charts: facts, not vectors.

GitHub has no ``documents`` rows, so it gets its own recorder that reads live
and writes the same ``activity_facts`` shape, so one SQL path serves every
chart. A counter is not a chunk: "GitHub embeds nothing" still holds
(``tests/test_insights_github.py``). Facts, not a live read per view: no rate
limit per viewer, and history beyond one call; freshness is disclosed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config.settings import GitHubLiveSettings
from ..githublive.base import dedupe_reviews
from ..db.connection import get_connection
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

PROVIDER = "github"

#: How far back a sync looks: a quarter of history after one sync, short
#: enough that the pull-request cap rarely binds.
WINDOW_DAYS = 180

#: The three people a pull request involves, kept as three kinds rather than
#: one "activity" kind with a role column: a chart must never be able to sum
#: them by accident, because "ada did 12 things" is not a fact anyone asked for.
KIND_OPENED = "pr_opened"
KIND_MERGED = "pr_merged"
KIND_REVIEWED = "pr_reviewed"
KIND_COMMIT = "commit"


@dataclass(frozen=True)
class GitHubFactsResult:
    """What one sync recorded, and whether it saw everything."""

    written: int = 0
    repos: int = 0
    #: True when any repo hit the pull-request cap. Returned rather than
    #: stashed in module state so the caller logs it and tests can see it.
    truncated: bool = False


def record_github_facts(
    org_id: str,
    *,
    workspace_id: str | None,
    reader=None,
    settings: GitHubLiveSettings | None = None,
) -> GitHubFactsResult:
    """Read this connection's pull requests and record them as facts.

    Never raises: it runs on the shared worker tick, and one revoked
    installation must not take it down. A failure costs a stale chart.
    """
    settings = settings or GitHubLiveSettings.from_env()
    since = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)

    try:
        if reader is None:
            from ..githublive import build_github_reader, refresh_installation_scope

            # Hourly re-read of the repo list: picks up added repos and each
            # one's `private` flag, which per-asker access depends on.
            try:
                refresh_installation_scope(org_id, workspace_id)
            except Exception:  # noqa: BLE001 - the stored scope still works
                logger.warning("insights: could not refresh GitHub scope for %s", org_id)
            # Charts read a full page of commits; the live Ask prompt keeps
            # its own small `max_commits`.
            from dataclasses import replace as _replace

            reader = build_github_reader(
                org_id, workspace_id,
                settings=_replace(settings, max_commits=settings.chart_max_commits),
            )
        repos = reader.list_repos()
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning(
            "insights: could not open GitHub for org %s (workspace=%s)",
            org_id, workspace_id, exc_info=True,
        )
        return GitHubFactsResult()

    rows: list[tuple] = []
    truncated = False
    seen_repos = 0

    for repo in repos:
        # Pull requests and commits are separate permissions, read
        # independently: one unreadable capability must never cost the other.
        page = None
        read_something = False
        try:
            page = reader.list_pull_requests(
                repo.full_name, since=since, limit=settings.max_pull_requests
            )
        except Exception:  # noqa: BLE001
            # One inaccessible repo (404/403, not retryable) must not cost the others.
            logger.warning(
                "insights: could not read pull requests of %s", repo.full_name,
                exc_info=True,
            )

        if page is not None:
            read_something = True
            truncated = truncated or page.truncated

            # The list omits `merged_by` and sometimes `merged_at`; filled from
            # the single pull request on a bounded slice.
            mergers = _fill_mergers(
                reader, page.items, limit=settings.max_reviewed_pull_requests,
            )
            for pull in page.items:
                rows.extend(
                    _pull_rows(org_id, workspace_id, mergers.get(pull.number, pull))
                )

            # Reviews are one call PER pull request, so the pull-request set is
            # bounded FIRST and only the newest slice is reviewed. A chart of
            # who reviews is stable well before 100 samples.
            for pull in page.items[: settings.max_reviewed_pull_requests]:
                try:
                    reviews = reader.list_reviews(repo.full_name, pull.number)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "insights: could not read reviews on %s#%s",
                        repo.full_name, pull.number, exc_info=True,
                    )
                    continue
                rows.extend(_review_rows(org_id, workspace_id, pull, reviews))

        try:
            commits = reader.list_commits(
                repo.full_name,
                since=since.isoformat(),
                limit=settings.chart_max_commits,
            )
            read_something = True
        except Exception:  # noqa: BLE001
            logger.warning(
                "insights: could not read commits of %s", repo.full_name,
                exc_info=True,
            )
            commits = []
        for commit in commits or []:
            rows.extend(_commit_rows(org_id, workspace_id, commit))

        # Counts a repo we could read SOMETHING from, so the log distinguishes
        # "no activity" from "no access".
        if read_something:
            seen_repos += 1

    written = _write(rows, workspace_id)
    logger.info(
        "insights: recorded %s GitHub facts across %s repos for org %s%s",
        written, seen_repos, org_id, " (capped)" if truncated else "",
    )
    return GitHubFactsResult(written=written, repos=seen_repos, truncated=truncated)


def actor_key(login: str | None) -> str | None:
    """``github:<login>``, the identity the graph joins on. Lowercased (logins
    are case-insensitive); built only from a LOGIN, never a display name."""
    login = (login or "").strip()
    return f"github:{login.lower()}" if login else None


def _pull_rows(org_id, workspace_id, pull) -> list[tuple]:
    """One row for raising it, and one for merging it if it merged. Per-kind
    ``external_id``, so re-reading a window updates instead of doubling."""
    attrs = _pull_attrs(pull)
    rows = [(
        org_id, workspace_id, PROVIDER, KIND_OPENED,
        pull.author, pull.repo, pull.state,
        pull.created_at, None, pull.url,
        f"{pull.repo}#{pull.number}", attrs,
        actor_key(pull.author),
    )]
    if pull.merged_at:
        # `merged_by` may be None (a deleted account, an automation). The merge
        # still happened, so it still counts -- it just leaves the per-person
        # breakdown rather than being credited to the wrong person.
        rows.append((
            org_id, workspace_id, PROVIDER, KIND_MERGED,
            pull.merged_by, pull.repo, "merged",
            pull.merged_at, pull.lead_time_seconds, pull.url,
            f"{pull.repo}#{pull.number}", attrs,
            actor_key(pull.merged_by),
        ))
    return rows


def _pull_attrs(pull) -> Jsonb:
    """Every simple field of the pull request (`insights.fields`) from the
    payload in hand (the detail payload for a merged PR). An absent value is
    omitted, never stored as "none", which would chart as a real value."""
    return Jsonb({
        k: list(v) if isinstance(v, tuple) else v
        for k, v in (getattr(pull, "fields", ()) or ())
    })

def _review_rows(org_id, workspace_id, pull, reviews) -> list[tuple]:
    """One row per reviewer per pull request, keeping each person's VERDICT
    (``githublive.base.dedupe_reviews``), not their first event."""
    rows = []
    for review in dedupe_reviews(reviews):
        rows.append((
            org_id, workspace_id, PROVIDER, KIND_REVIEWED,
            review.reviewer, pull.repo, review.state,
            review.submitted_at or pull.created_at, None, pull.url,
            f"{pull.repo}#{pull.number}:{review.reviewer}", Jsonb({}),
            actor_key(review.reviewer),
        ))
    return rows


def _commit_rows(org_id, workspace_id, commit) -> list[tuple]:
    """One row per commit. Skipped when GitHub gave no date — stamping now()
    would pile undated commits onto today's bar."""
    if not getattr(commit, "date", None) or not getattr(commit, "sha", None):
        return []
    return [(
        org_id, workspace_id, PROVIDER, KIND_COMMIT,
        commit.author, commit.repo, None,
        commit.date, None, commit.url,
        f"{commit.repo}:{commit.sha}", Jsonb({}),
        # Only a real login: `author` may be the git display name.
        actor_key(getattr(commit, "author_login", None)),
    )]


def _write(rows: list[tuple], workspace_id: str | None) -> int:
    """Upsert every row in one statement. The conflict target must match the
    scope's PARTIAL unique index (NULLs are distinct in a plain UNIQUE)."""
    if not rows:
        return 0

    if workspace_id is None:
        conflict = """
            ON CONFLICT (org_id, provider, kind, external_id)
                WHERE workspace_id IS NULL AND external_id IS NOT NULL
        """
    else:
        conflict = """
            ON CONFLICT (org_id, workspace_id, provider, kind, external_id)
                WHERE workspace_id IS NOT NULL AND external_id IS NOT NULL
        """

    sql = f"""
        INSERT INTO activity_facts
            (org_id, workspace_id, provider, kind, actor, subject, state,
             occurred_at, value, url, external_id, attrs, actor_key)
        VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        {conflict}
        DO UPDATE SET actor       = EXCLUDED.actor,
                      actor_key   = EXCLUDED.actor_key,
                      attrs       = EXCLUDED.attrs,
                      state       = EXCLUDED.state,
                      occurred_at = EXCLUDED.occurred_at,
                      value       = EXCLUDED.value,
                      url         = EXCLUDED.url
    """

    # Row by row, each in its own savepoint: one refused row must not roll back
    # the whole sync.
    written = failed = 0
    try:
        with get_connection() as conn:
            for row in rows:
                try:
                    with conn.transaction():
                        conn.execute(sql, row)
                    written += 1
                except Exception:  # noqa: BLE001 - skip the row, keep the rest
                    failed += 1
                    if failed == 1:
                        logger.warning("insights: could not write GitHub fact %s %s",
                                       row[3], row[10], exc_info=True)
            conn.commit()
    except Exception:  # noqa: BLE001 - a stale chart, never a failed tick
        logger.warning("insights: could not write GitHub facts", exc_info=True)
        return written
    if failed:
        logger.warning("insights: skipped %s of %s GitHub facts that could not be stored",
                       failed, len(rows))
    return written


def _needs_pull_detail(pull) -> bool:
    """A list row that cannot yet say whether it merged, or who merged it:
    only the single pull request settles both."""
    if pull.merged_at and pull.merged_by:
        return False
    if pull.merged_at:
        return True
    return (getattr(pull, "state", None) or "").lower() == "closed"


def _fill_mergers(reader, pulls, *, limit: int) -> dict[int, object]:
    """``{number: detailed pull}`` for the rows the list did not finish.

    At most ``limit`` calls. A failure is skipped; a merge with ``merged_at``
    still counts, only without the per-person breakdown.
    """
    detail = getattr(reader, "get_pull_request", None)
    if detail is None:
        return {}

    out: dict[int, object] = {}
    asked = 0
    for pull in pulls:
        if not _needs_pull_detail(pull):
            continue
        if asked >= limit:
            break
        asked += 1
        try:
            full = detail(pull.repo, pull.number)
        except Exception:  # noqa: BLE001 - a gap in one chart, never a failed sync
            logger.debug(
                "insights: could not read %s#%s",
                pull.repo, pull.number, exc_info=True,
            )
            continue
        if full is not None:
            out[pull.number] = full
    return out


# ---------------------------------------------------------------------------
# On demand: a GitHub chart reads GitHub when it is asked
# ---------------------------------------------------------------------------

import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _Waited

_READS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="github-chart-read")
_RUNNING: dict[tuple[str, str | None], object] = {}
_RUNNING_LOCK = threading.Lock()


def _connection(org_id: str, workspace_id: str | None):
    """``(id, last_read_at)`` of this scope's GitHub connection, or None."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT id::text, last_sync_at FROM oauth_connections "
            "WHERE org_id = %s::uuid AND provider = 'github' AND needs_reauth = false "
            "AND workspace_id IS NOT DISTINCT FROM %s::uuid LIMIT 1",
            (org_id, workspace_id),
        ).fetchone()


def _read_now(org_id: str, workspace_id: str | None, connection_id: str) -> None:
    record_github_facts(org_id, workspace_id=workspace_id)
    with get_connection() as conn:
        conn.execute("UPDATE oauth_connections SET last_sync_at = now() WHERE id = %s::uuid",
                     (connection_id,))
        conn.commit()


def refresh_for_chart(org_id: str, workspace_id: str | None,
                      settings: GitHubLiveSettings | None = None) -> str:
    """Read GitHub now, for a chart being asked. Returns what happened:

    - ``"fresh"``    read within ``chart_refresh_minutes``; nothing to do;
    - ``"refreshed"`` read just now, within the wait;
    - ``"reading"``  still reading after ``chart_refresh_wait_seconds``: the
      chart answers from what was last read and the read finishes behind it;
    - ``"failed"``   GitHub could not be read; the chart uses what it has;
    - ``"none"``     no GitHub connection in this scope.

    Counting stays SQL over stored rows; only WHEN they are read changes. One
    read per scope at a time. Never raises.
    """
    from datetime import datetime, timedelta, timezone

    settings = settings or GitHubLiveSettings.from_env()
    try:
        row = _connection(org_id, workspace_id)
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("insights: could not look up the GitHub connection", exc_info=True)
        return "failed"
    if row is None:
        return "none"
    connection_id, last = row
    if last is not None and settings.chart_refresh_minutes > 0:
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last > datetime.now(timezone.utc) - timedelta(minutes=settings.chart_refresh_minutes):
            return "fresh"
    key = (org_id, workspace_id)
    with _RUNNING_LOCK:
        running = _RUNNING.get(key)
        if running is None or running.done():
            running = _READS.submit(_read_now, org_id, workspace_id, connection_id)
            _RUNNING[key] = running
    try:
        running.result(timeout=settings.chart_refresh_wait_seconds)
    except _Waited:
        return "reading"
    except Exception:  # noqa: BLE001 - see docstring
        logger.warning("insights: on-demand GitHub read failed", exc_info=True)
        return "failed"
    return "refreshed"
