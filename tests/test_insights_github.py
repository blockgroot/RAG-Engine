"""GitHub charts: facts, not vectors.

Two guarantees are asserted here rather than left as comments, because both are
the kind that decay silently:

1. **GitHub still never reaches the ingestion queue.** ``UNSYNCABLE_PROVIDERS``
   exists because a GitHub ingestion job can only ever fail with "Unknown
   source type: 'github'" -- which happened in production. A facts path must
   not quietly re-open that door.
2. **"GitHub embeds nothing" still holds.** That rule is about VECTORS: no
   documents, no chunks, no embeddings, no SourceAdapter. A counter is not a
   chunk, and the distinction only stays true if something checks it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import get_connection
from app.githublive.base import PullRequest, PullRequestPage, Review
from app.insights import github_facts, store
from .conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"gh-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _pr(number, *, author="ada", merged_by=None, created_days=10,
        merged_days=None, repo="acme/api"):
    now = datetime.now(timezone.utc)
    return PullRequest(
        repo=repo,
        number=number,
        title=f"PR {number}",
        author=author,
        merged_by=merged_by,
        state="merged" if merged_days is not None else "open",
        created_at=now - timedelta(days=created_days),
        merged_at=None if merged_days is None else now - timedelta(days=merged_days),
        closed_at=None if merged_days is None else now - timedelta(days=merged_days),
        url=f"https://github.com/{repo}/pull/{number}",
    )


class FakeReader:
    """Only what github_facts actually calls."""

    def __init__(self, pulls, reviews=None, truncated=False, commits=None):
        self._page = PullRequestPage(items=tuple(pulls), truncated=truncated)
        self._reviews = reviews or {}
        self._commits = commits or []
        self.repos_listed = 0
        self.review_calls: list[int] = []
        self.commit_calls: list[str] = []

    def list_repos(self):
        from app.githublive.repos import RepoRef

        self.repos_listed += 1
        return [RepoRef(full_name="acme/api", description=None, topics=())]

    def list_pull_requests(self, repo, *, since=None, limit=100):
        return self._page

    def list_reviews(self, repo, pull_number):
        self.review_calls.append(pull_number)
        return self._reviews.get(pull_number, [])

    def list_commits(self, repo, *, path=None, since=None, limit=10):
        self.commit_calls.append(repo)
        return list(self._commits)


def _total(key, org_id, **kw):
    rows = store.run_metric(key, org_id=org_id, workspace_id=None,
                            period="month", days=365, **kw)
    return sum(r.value for r in rows)


# --------------------------------------------------------------------------
# The two structural guarantees
# --------------------------------------------------------------------------


def test_github_writes_facts_but_no_documents_and_no_chunks(org):
    """"GitHub embeds nothing" is about vectors. Facts are counters."""
    reader = FakeReader([_pr(1, merged_by="grace", merged_days=8)])
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)

    with get_connection() as conn:
        facts = conn.execute(
            "SELECT count(*) FROM activity_facts WHERE org_id = %s AND provider = 'github'",
            (org,),
        ).fetchone()[0]
        docs = conn.execute(
            "SELECT count(*) FROM documents WHERE org_id = %s AND source_provider = 'github'",
            (org,),
        ).fetchone()[0]
        chunks = conn.execute(
            """
            SELECT count(*) FROM chunks c JOIN documents d ON d.id = c.document_id
             WHERE c.org_id = %s AND d.source_provider = 'github'
            """,
            (org,),
        ).fetchone()[0]

    assert facts > 0, "GitHub must produce countable facts"
    assert docs == 0, "GitHub must never create a documents row"
    assert chunks == 0, "GitHub must never create a chunk"


def test_github_is_still_excluded_from_the_ingestion_queue():
    """The constant that stopped 'Unknown source type: github' in production
    must survive this feature."""
    from app.jobs.autosync import UNSYNCABLE_PROVIDERS

    assert "github" in UNSYNCABLE_PROVIDERS


# --------------------------------------------------------------------------
# The three distinct people
# --------------------------------------------------------------------------


def test_the_author_and_the_merger_are_counted_separately(org):
    """On most teams one person merges most pull requests. That reading is
    invisible if "actor" silently means "author" everywhere."""
    reader = FakeReader([
        _pr(1, author="ada", merged_by="grace", merged_days=5),
        _pr(2, author="linus", merged_by="grace", merged_days=4),
    ])
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)

    opened = store.run_metric("prs_opened", org_id=org, workspace_id=None,
                              period="month", days=365, group_by="actor")
    merged = store.run_metric("prs_merged", org_id=org, workspace_id=None,
                              period="month", days=365, group_by="actor")

    assert {r.group for r in opened} == {"ada", "linus"}
    assert {r.group for r in merged} == {"grace"}


def test_reviewers_come_from_the_reviews_call(org):
    reviews = {
        1: [Review(repo="acme/api", pull_number=1, reviewer="grace",
                   state="APPROVED", submitted_at=datetime.now(timezone.utc))],
    }
    reader = FakeReader([_pr(1, author="ada", merged_by="ada", merged_days=3)], reviews)
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)

    rows = store.run_metric("pr_reviewers", org_id=org, workspace_id=None,
                            period="month", days=365, group_by="actor")
    assert {r.group for r in rows} == {"grace"}


def test_an_unmerged_pull_request_is_never_counted_as_merged(org):
    """GitHub reports a merged pull request as "closed", so counting merges off
    its state would count every abandoned branch as a merge."""
    reader = FakeReader([_pr(1, author="ada", merged_days=None)])
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)

    assert _total("prs_opened", org) == 1
    assert _total("prs_merged", org) == 0


def test_commits_are_counted_by_author_and_never_create_documents(org):
    from app.githublive.base import CommitSummary

    when = datetime.now(timezone.utc) - timedelta(days=2)
    reader = FakeReader(
        [_pr(1, merged_days=None)],
        commits=[
            CommitSummary(
                repo="acme/api",
                sha="aaa111",
                message="fix login",
                author="ada",
                date=when,
                url="https://github.com/acme/api/commit/aaa111",
            ),
            CommitSummary(
                repo="acme/api",
                sha="bbb222",
                message="tweak copy",
                author="ada",
                date=when,
                url="https://github.com/acme/api/commit/bbb222",
            ),
        ],
    )
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)

    assert reader.commit_calls == ["acme/api"]
    rows = store.run_metric(
        "commits_by_author", org_id=org, workspace_id=None,
        period="month", days=365, group_by="actor",
    )
    assert {r.group: r.value for r in rows} == {"ada": 2.0}

    with get_connection() as conn:
        docs = conn.execute(
            "SELECT count(*) FROM documents WHERE org_id = %s AND source_provider = 'github'",
            (org,),
        ).fetchone()[0]
    assert docs == 0


def test_a_commit_without_a_date_is_not_stamped_today(org):
    from app.githublive.base import CommitSummary

    reader = FakeReader(
        [],
        commits=[
            CommitSummary(
                repo="acme/api",
                sha="no-date",
                message="mystery",
                author="ada",
                date=None,
                url="https://github.com/acme/api/commit/no-date",
            ),
        ],
    )
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)
    assert _total("commits_by_author", org) == 0


def test_a_merge_with_no_named_merger_still_counts_the_merge(org):
    """GitHub omits `merged_by` for some merges (a deleted account, an
    automation). The merge happened, so it counts -- it just leaves the
    per-person breakdown rather than being credited to someone."""
    reader = FakeReader([_pr(1, author="ada", merged_by=None, merged_days=2)])
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)

    assert _total("prs_merged", org) == 1
    rows = store.run_metric("prs_merged", org_id=org, workspace_id=None,
                            period="month", days=365, group_by="actor")
    assert [r.group for r in rows] == [None]


# --------------------------------------------------------------------------
# Lead time, bounds, idempotence
# --------------------------------------------------------------------------


def test_lead_time_is_stored_in_seconds_on_the_merge_fact(org):
    reader = FakeReader([_pr(1, author="ada", merged_by="ada",
                             created_days=10, merged_days=8)])
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)

    with get_connection() as conn:
        value = conn.execute(
            "SELECT value FROM activity_facts "
            " WHERE org_id = %s AND kind = 'pr_merged'",
            (org,),
        ).fetchone()[0]
    assert abs(float(value) - 2 * 86400) < 120, "two days, in seconds"


def test_an_open_pull_request_has_no_lead_time(org):
    """Treating "now" as the end would make every stale branch look like a slow
    merge, and the p90 would drift upward on its own every day."""
    reader = FakeReader([_pr(1, merged_days=None)])
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT value FROM activity_facts WHERE org_id = %s AND kind = 'pr_opened'",
            (org,),
        ).fetchall()
    assert all(r[0] is None for r in rows)


def test_reviews_are_only_fetched_for_a_bounded_number_of_pull_requests(org):
    """Reviews cost ONE call per pull request. Without a bound, a repo with 100
    open pull requests is 100 extra calls on every sync."""
    from app.config.settings import GitHubLiveSettings

    settings = GitHubLiveSettings.from_env()
    pulls = [_pr(n, merged_by="grace", merged_days=n % 20 + 1) for n in range(1, 61)]
    reader = FakeReader(pulls)
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)

    assert len(reader.review_calls) <= settings.max_reviewed_pull_requests


def test_re_recording_the_same_pull_requests_does_not_double_the_count(org):
    """Every sync re-reads the same window, so this runs constantly."""
    reader = FakeReader([_pr(1, author="ada", merged_by="grace", merged_days=5)])
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)
    github_facts.record_github_facts(org, workspace_id=None, reader=reader)

    assert _total("prs_opened", org) == 1
    assert _total("prs_merged", org) == 1


def test_a_reader_failure_never_raises_out_of_the_recorder(org):
    """This runs on the shared worker tick beside the ingestion queue and the
    scheduler. One dead installation must not take those down."""
    class Broken:
        def list_repos(self):
            raise RuntimeError("installation revoked")

    result = github_facts.record_github_facts(org, workspace_id=None, reader=Broken())
    assert result.written == 0


def test_every_github_metric_discloses_its_cap(org):
    """A chart built from the first N while believing it complete is the failure
    that matters. The cap is stated on the panel itself -- static rather than a
    per-run note, because it is true on every run, truncated or not."""
    from app.insights import registry

    for metric in registry.for_provider("github"):
        assert metric.caveat, f"{metric.key} discloses no coverage limit"


def test_truncation_is_returned_so_the_caller_can_log_it(org):
    reader = FakeReader([_pr(1, merged_by="g", merged_days=1)], truncated=True)
    result = github_facts.record_github_facts(org, workspace_id=None, reader=reader)
    assert result.truncated is True
    assert result.written >= 1


# --------------------------------------------------------------------------
# The autosync branch. GitHub is due like anything else, but it must reach the
# facts recorder and NEVER the ingestion queue.
# --------------------------------------------------------------------------


def _connection(org_id, provider, *, last_sync=None, needs_reauth=False):
    with get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO oauth_connections
                (org_id, provider, external_workspace_id, access_token_encrypted,
                 last_sync_at, needs_reauth)
            VALUES (%s, %s, %s, 'x', %s, %s) RETURNING id::text
            """,
            (org_id, provider, uuid.uuid4().hex, last_sync, needs_reauth),
        ).fetchone()
        conn.commit()
    return row[0]


def test_a_github_connection_is_due_for_facts_not_for_ingestion(org):
    """Both constants name github and both are right: it is excluded from the
    ingestion queue and included in the facts path."""
    from app.config.settings import AutoSyncSettings
    from app.jobs import autosync

    _connection(org, "github", last_sync=None)
    settings = AutoSyncSettings.from_env()

    ingest_due = [c for c in autosync._due_connections(settings) if c[1] == org]
    facts_due = [c for c in autosync._due_facts_connections(settings) if c[1] == org]

    assert ingest_due == [], "github must never be queued for ingestion"
    assert len(facts_due) == 1, "github must be due for facts"


def test_a_dead_github_token_is_skipped_by_the_facts_path_too(org):
    """A revoked installation cannot be fixed by retrying it, and hammering one
    is how an org gets rate-limited for a problem only a reconnect solves."""
    from app.config.settings import AutoSyncSettings
    from app.jobs import autosync

    _connection(org, "github", last_sync=None, needs_reauth=True)
    due = [c for c in autosync._due_facts_connections(AutoSyncSettings.from_env())
           if c[1] == org]
    assert due == []


def test_recording_facts_stamps_the_attempt_so_it_does_not_requalify(org, monkeypatch):
    """Stamped on ATTEMPT, not success -- one failed sync costs one interval of
    freshness, which is visible; a hot retry loop is not."""
    from app.jobs import autosync

    connection_id = _connection(org, "github", last_sync=None)
    monkeypatch.setattr(
        autosync, "_due_facts_connections",
        lambda settings: [(connection_id, org, None, "interval")],
    )
    monkeypatch.setattr(
        "app.insights.github_facts.record_github_facts",
        lambda *a, **k: github_facts.GitHubFactsResult(written=3),
    )

    assert autosync.record_due_facts() == 1

    with get_connection() as conn:
        stamped = conn.execute(
            "SELECT last_sync_at FROM oauth_connections WHERE id = %s",
            (connection_id,),
        ).fetchone()[0]
    assert stamped is not None


def test_the_external_tick_reports_facts_separately_from_queued_syncs():
    """The tick's return value is what the cron's own log shows, so "GitHub was
    read" must be distinguishable from "an ingest was queued"."""
    from app.jobs.worker import run_external_tick

    result = run_external_tick()
    assert "facts_recorded" in result
    assert "syncs_queued" in result
