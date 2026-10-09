"""GitHub charts: facts, not vectors.

GitHub is the one connector with no ``SourceAdapter`` and no ``documents`` rows
-- it embeds nothing -- so ``facts.record_document_facts`` can never see it. It
gets its own recorder, reading live and writing the SAME ``activity_facts``
shape everything else uses, which is what lets one SQL path serve every chart.

**This does not break "GitHub embeds nothing."** That rule is about vectors: no
documents, no chunks, no embeddings, no adapter. A counter is not a chunk, and
``tests/test_insights_github.py`` asserts the distinction in both directions.

Why facts rather than reading live at view time (which is what the first draft
of this feature did): a page load would pay GitHub's rate limit per viewer, add
a cold start plus N API calls of latency, and could show no history beyond what
one cheap call returns. The cost is that GitHub charts are as fresh as the last
sync rather than live -- invisible for "pull requests merged per week", and
disclosed by the freshness panel regardless.
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

#: How far back a sync looks. Long enough that a quarterly chart has history
#: after one sync, short enough that the pull-request cap is rarely the binding
#: constraint. Re-read on every sync, so a fact's date only ever gets more
#: accurate.
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

    Never raises. This runs on the shared worker tick beside the ingestion
    queue and the activity scheduler, and one revoked installation must not
    take those down -- the same reason ``enqueue_due_syncs`` is broad. A
    failure costs a stale chart, which the freshness panel already discloses.
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
        # Pull requests and commits are read INDEPENDENTLY. They are separate
        # GitHub permissions, and an installation routinely has one without
        # the other -- so a `continue` here silently discarded every commit in
        # a repo whose pull requests 403'd, which is how a tenant with four
        # readable commits ended up with an empty `activity_facts` and a
        # "nothing recorded yet" chart. One unreadable capability must never
        # cost a readable one.
        page = None
        read_something = False
        try:
            page = reader.list_pull_requests(
                repo.full_name, since=since, limit=settings.max_pull_requests
            )
        except Exception:  # noqa: BLE001
            # One inaccessible repo must not cost the others. GitHub 404s what
            # a token cannot see and 403s what the installation may not read;
            # neither is retryable.
            logger.warning(
                "insights: could not read pull requests of %s", repo.full_name,
                exc_info=True,
            )

        if page is not None:
            read_something = True
            truncated = truncated or page.truncated

            # `merged_by` is absent from the LIST payload, so a merger can only
            # be read one pull request at a time. Bounded to the same slice
            # reviews already pay for -- without it the "who merges them" chart
            # is empty on every tenant, which reads as "nobody merges" rather
            # than as a gap.
            mergers = _fill_mergers(
                reader, page.items[: settings.max_reviewed_pull_requests]
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
    """``github:<login>`` — the identity the Second Brain graph joins on.

    Lowercased because GitHub logins are case-insensitive, so ``Ada`` in a
    review and ``ada`` on a pull request are one account. Only ever built from
    a LOGIN; a caller holding a display name passes None.
    """
    login = (login or "").strip()
    return f"github:{login.lower()}" if login else None


def _pull_rows(org_id, workspace_id, pull) -> list[tuple]:
    """One row for raising it, and one for merging it if it merged.

    ``external_id`` is per kind, so the two rows cannot collide on the unique
    index -- and re-reading the same window updates them instead of doubling.
    """
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
    """Every simple field of the pull request (`insights.fields`), as parsed
    from the payload already in hand -- no extra call. For a merged PR that
    is the DETAIL payload `_fill_mergers` fetched, which also carries
    additions, deletions and changed files. An absent value is omitted,
    never stored as "none" -- that would chart as a real value."""
    return Jsonb({
        k: list(v) if isinstance(v, tuple) else v
        for k, v in (getattr(pull, "fields", ()) or ())
    })

def _review_rows(org_id, workspace_id, pull, reviews) -> list[tuple]:
    """One row per reviewer per pull request, not per review event.

    Deduplication keeps each person's VERDICT rather than their first event --
    see ``githublive.base.dedupe_reviews``. Storing the first would record
    "commented, then approved" as COMMENTED, and the state is what a chart of
    approvals reads.
    """
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
    """Upsert every row in one statement.

    The conflict target must match one of the two PARTIAL unique indexes, and
    which applies depends on the scope -- Postgres treats NULLs as distinct in
    a plain UNIQUE, which is why they are partial.
    """
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

    # Row by row, each in its own savepoint: one row the database refuses (a
    # commit with no date, a value it cannot store) used to roll back the
    # WHOLE batch, so a single odd commit silently cost every pull request of
    # every repo in that sync.
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


def _fill_mergers(reader, pulls) -> dict[int, object]:
    """``{number: detailed pull}`` for the merged ones we can enrich.

    One call each, so the caller passes an already-bounded slice. A failure is
    skipped rather than raised: the pull request still counts as merged, it
    just leaves the per-person breakdown -- the same choice ``_pull_rows``
    makes for a merge with no `merged_by` at all.
    """
    detail = getattr(reader, "get_pull_request", None)
    if detail is None:
        return {}

    out: dict[int, object] = {}
    for pull in pulls:
        if not pull.merged_at or pull.merged_by:
            continue
        try:
            full = detail(pull.repo, pull.number)
        except Exception:  # noqa: BLE001 - a gap in one chart, never a failed sync
            logger.debug(
                "insights: could not read %s#%s for its merger",
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

    GitHub has no "count merged PRs per week" call, so the counting stays SQL
    over stored rows (one checked, access-filtered path for every chart); what
    changes is WHEN they are read: when someone asks, not on a timer. One read
    per scope at a time. Never raises.
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
