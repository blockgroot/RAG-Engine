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
            from ..githublive import build_github_reader

            reader = build_github_reader(org_id, workspace_id, settings=settings)
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
        try:
            page = reader.list_pull_requests(
                repo.full_name, since=since, limit=settings.max_pull_requests
            )
        except Exception:  # noqa: BLE001
            # One inaccessible repo must not cost the others. GitHub 404s what
            # a token cannot see, and that is not retryable.
            logger.warning(
                "insights: could not read pull requests of %s", repo.full_name,
                exc_info=True,
            )
            continue

        seen_repos += 1
        truncated = truncated or page.truncated

        # `merged_by` is absent from the LIST payload, so a merger can only be
        # read one pull request at a time. Bounded to the same slice reviews
        # already pay for -- without it the "who merges them" chart is empty on
        # every tenant, which reads as "nobody merges" rather than as a gap.
        mergers = _fill_mergers(
            reader, page.items[: settings.max_reviewed_pull_requests]
        )
        for pull in page.items:
            rows.extend(
                _pull_rows(org_id, workspace_id, mergers.get(pull.number, pull))
            )

        # Reviews are one call PER pull request, so the pull-request set is
        # bounded FIRST and only the newest slice is reviewed. A chart of who
        # reviews is stable well before 100 samples.
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
                limit=settings.max_commits,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "insights: could not read commits of %s", repo.full_name,
                exc_info=True,
            )
            commits = []
        for commit in commits or []:
            rows.extend(_commit_rows(org_id, workspace_id, commit))

    written = _write(rows, workspace_id)
    logger.info(
        "insights: recorded %s GitHub facts across %s repos for org %s%s",
        written, seen_repos, org_id, " (capped)" if truncated else "",
    )
    return GitHubFactsResult(written=written, repos=seen_repos, truncated=truncated)


def _pull_rows(org_id, workspace_id, pull) -> list[tuple]:
    """One row for raising it, and one for merging it if it merged.

    ``external_id`` is per kind, so the two rows cannot collide on the unique
    index -- and re-reading the same window updates them instead of doubling.
    """
    rows = [(
        org_id, workspace_id, PROVIDER, KIND_OPENED,
        pull.author, pull.repo, pull.state,
        pull.created_at, None, pull.url,
        f"{pull.repo}#{pull.number}",
    )]
    if pull.merged_at:
        # `merged_by` may be None (a deleted account, an automation). The merge
        # still happened, so it still counts -- it just leaves the per-person
        # breakdown rather than being credited to the wrong person.
        rows.append((
            org_id, workspace_id, PROVIDER, KIND_MERGED,
            pull.merged_by, pull.repo, "merged",
            pull.merged_at, pull.lead_time_seconds, pull.url,
            f"{pull.repo}#{pull.number}",
        ))
    return rows


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
            f"{pull.repo}#{pull.number}:{review.reviewer}",
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
        f"{commit.repo}:{commit.sha}",
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
             occurred_at, value, url, external_id)
        VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        {conflict}
        DO UPDATE SET actor       = EXCLUDED.actor,
                      state       = EXCLUDED.state,
                      occurred_at = EXCLUDED.occurred_at,
                      value       = EXCLUDED.value,
                      url         = EXCLUDED.url
    """

    try:
        with get_connection() as conn:
            conn.cursor().executemany(sql, rows)
            conn.commit()
    except Exception:  # noqa: BLE001 - a stale chart, never a failed tick
        logger.warning("insights: could not write GitHub facts", exc_info=True)
        return 0
    return len(rows)


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
