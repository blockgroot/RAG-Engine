"""The live GitHub read layer (Plan Phase 5).

This is the whole GitHub data path — there is no adapter, no ingestion, and no
vector store involved, so these tests are the primary correctness evidence for
how GitHub content reaches an answer.

Three properties matter most, and each has dedicated cases:

1. **The allowlist runs before the network** (T1). Every operation takes a
   ``repo`` the LLM filled in, so a foreign or malformed name must be refused
   without an authenticated request ever leaving the process.
2. **Nothing unbounded reaches a prompt** (T6). A README can be huge and a
   commit diff can span thousands of files, so both are truncated to a byte
   budget with an explicit marker — a silently-cut payload would let the model
   answer confidently from half the evidence.
3. **Transient failures retry; permanent ones don't** (T5). 429/5xx back off;
   404 is terminal because GitHub returns it for both "missing" and "not
   visible to this token".
"""

from __future__ import annotations

import pytest

from app.config.settings import GitHubLiveSettings
from app.core.exceptions import SourceError
from app.githublive import InstallationScope, RepoRef
from app.githublive.rest import RestGitHubReader


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Backoff must not actually slow the suite down."""
    monkeypatch.setattr("app.githublive.rest.time.sleep", lambda _s: None)


def _scope(selection: str = "selected") -> InstallationScope:
    return InstallationScope(
        installation_id="4242",
        account_login="acme-inc",
        repository_selection=selection,
        repos=(RepoRef("acme-inc/handbook", "Eng handbook"), RepoRef("acme-inc/payments-svc")),
    )


def _reader(monkeypatch, responses, *, settings: GitHubLiveSettings | None = None):
    """Build a reader whose HTTP calls are served from a queue of fakes."""
    calls: list = []

    class _Resp:
        def __init__(self, spec):
            self.status_code = spec.get("status", 200)
            self._json = spec.get("json", {})
            self.text = spec.get("text", "")
            self.headers = spec.get("headers", {})

        def json(self):
            return self._json

    queue = list(responses)

    def _get(url, headers=None, params=None, timeout=None):
        calls.append({"url": url, "params": params or {}, "headers": headers or {}})
        return _Resp(queue.pop(0) if queue else {"status": 200, "json": {}})

    monkeypatch.setattr("app.githublive.rest.httpx.get", _get)
    reader = RestGitHubReader(
        token="ghs_tok",
        scope=_scope(),
        settings=settings or GitHubLiveSettings(),
    )
    return reader, calls


# -- T1: the allowlist fires before any network call -----------------------


@pytest.mark.parametrize(
    "op",
    [
        lambda r: r.get_readme("other-org/secrets"),
        lambda r: r.get_commit("other-org/secrets", "abc123"),
        lambda r: r.list_commits("other-org/secrets"),
    ],
)
def test_every_operation_refuses_a_foreign_repo_before_calling_github(monkeypatch, op):
    reader, calls = _reader(monkeypatch, [])

    with pytest.raises(SourceError):
        op(reader)

    assert calls == [], "a refused repo must never produce an authenticated request"


def test_unauthorized_repo_of_the_same_owner_is_refused(monkeypatch):
    reader, calls = _reader(monkeypatch, [])

    with pytest.raises(SourceError):
        reader.get_readme("acme-inc/secret-payroll")

    assert calls == []


# -- get_readme ------------------------------------------------------------


def test_get_readme_returns_raw_markdown(monkeypatch):
    reader, calls = _reader(
        monkeypatch, [{"status": 200, "text": "# Handbook\n\nHow we work."}]
    )

    readme = reader.get_readme("handbook")  # bare name gets qualified

    assert readme.repo == "acme-inc/handbook"
    assert "How we work." in readme.content
    assert readme.truncated is False
    assert calls[0]["url"].endswith("/repos/acme-inc/handbook/readme")
    # Raw media type, else GitHub returns base64 JSON we'd have to decode.
    assert "raw" in calls[0]["headers"]["Accept"]


def test_get_readme_truncates_to_the_byte_budget_with_a_marker(monkeypatch):
    reader, _ = _reader(
        monkeypatch,
        [{"status": 200, "text": "x" * 5000}],
        settings=GitHubLiveSettings(readme_max_bytes=100),
    )

    readme = reader.get_readme("handbook")

    assert readme.truncated is True
    assert len(readme.content) < 5000
    assert "truncated" in readme.content.lower()


def test_get_readme_404_is_a_clear_source_error(monkeypatch):
    reader, _ = _reader(monkeypatch, [{"status": 404, "json": {"message": "Not Found"}}])

    with pytest.raises(SourceError) as excinfo:
        reader.get_readme("handbook")

    assert "acme-inc/handbook" in str(excinfo.value)


# -- get_commit ------------------------------------------------------------


def _commit_payload(files=None, message="Fix the login redirect loop"):
    return {
        "sha": "abc123def456",
        "html_url": "https://github.com/acme-inc/payments-svc/commit/abc123def456",
        "commit": {
            "message": message,
            "author": {"name": "Dev Eloper", "date": "2026-07-01T10:00:00Z"},
        },
        "stats": {"additions": 10, "deletions": 2, "total": 12},
        "files": files
        if files is not None
        else [
            {
                "filename": "auth/login.py",
                "status": "modified",
                "additions": 8,
                "deletions": 2,
                "patch": "@@ -1 +1 @@\n-old\n+new",
            }
        ],
    }


def test_get_commit_parses_message_author_and_files(monkeypatch):
    reader, calls = _reader(monkeypatch, [{"status": 200, "json": _commit_payload()}])

    commit = reader.get_commit("payments-svc", "abc123def456")

    assert commit.repo == "acme-inc/payments-svc"
    assert commit.sha == "abc123def456"
    assert "login redirect loop" in commit.message
    assert commit.author == "Dev Eloper"
    assert commit.files[0].path == "auth/login.py"
    assert commit.files[0].additions == 8
    assert "+new" in commit.files[0].patch
    assert calls[0]["url"].endswith("/repos/acme-inc/payments-svc/commits/abc123def456")


def test_get_commit_truncates_an_oversized_patch(monkeypatch):
    huge = [
        {
            "filename": "big.txt",
            "status": "modified",
            "additions": 9999,
            "deletions": 0,
            "patch": "+line\n" * 5000,
        }
    ]
    reader, _ = _reader(
        monkeypatch,
        [{"status": 200, "json": _commit_payload(files=huge)}],
        settings=GitHubLiveSettings(patch_max_bytes=200),
    )

    commit = reader.get_commit("payments-svc", "abc123")

    assert len(commit.files[0].patch) < 1000
    assert "truncated" in commit.files[0].patch.lower()


def test_get_commit_caps_the_number_of_files_reported(monkeypatch):
    many = [
        {"filename": f"f{i}.py", "status": "modified", "additions": 1, "deletions": 0}
        for i in range(50)
    ]
    reader, _ = _reader(
        monkeypatch,
        [{"status": 200, "json": _commit_payload(files=many)}],
        settings=GitHubLiveSettings(max_files_per_commit=10),
    )

    commit = reader.get_commit("payments-svc", "abc123")

    assert len(commit.files) == 10
    assert commit.files_truncated is True


def test_get_commit_rejects_a_malformed_sha(monkeypatch):
    """The sha is also model-supplied, so it can't go into a URL unchecked."""
    reader, calls = _reader(monkeypatch, [])

    with pytest.raises(SourceError):
        reader.get_commit("payments-svc", "../../../etc/passwd")

    assert calls == []


# -- list_commits ----------------------------------------------------------


def test_list_commits_returns_summaries_and_honours_path_and_limit(monkeypatch):
    payload = [
        {
            "sha": f"sha{i}",
            "html_url": f"https://github.com/acme-inc/payments-svc/commit/sha{i}",
            "commit": {
                "message": f"Change {i}\n\nlonger body that should not be included",
                "author": {"name": "Dev", "date": "2026-07-01T10:00:00Z"},
            },
        }
        for i in range(3)
    ]
    reader, calls = _reader(monkeypatch, [{"status": 200, "json": payload}])

    commits = reader.list_commits("payments-svc", path="auth/login.py", limit=3)

    assert [c.sha for c in commits] == ["sha0", "sha1", "sha2"]
    # Only the subject line — commit bodies would bloat the prompt.
    assert commits[0].message == "Change 0"
    assert calls[0]["params"]["path"] == "auth/login.py"
    assert calls[0]["params"]["per_page"] == 3


def test_list_commits_clamps_an_absurd_limit(monkeypatch):
    reader, calls = _reader(
        monkeypatch,
        [{"status": 200, "json": []}],
        settings=GitHubLiveSettings(max_commits=20),
    )

    reader.list_commits("payments-svc", limit=10_000)

    assert calls[0]["params"]["per_page"] == 20


# -- T5: retry behaviour ---------------------------------------------------


def test_rate_limited_request_is_retried_then_succeeds(monkeypatch):
    reader, calls = _reader(
        monkeypatch,
        [
            {"status": 429, "headers": {"Retry-After": "0"}, "json": {}},
            {"status": 200, "text": "# Handbook"},
        ],
    )

    readme = reader.get_readme("handbook")

    assert "Handbook" in readme.content
    assert len(calls) == 2


def test_server_error_is_retried(monkeypatch):
    reader, calls = _reader(
        monkeypatch,
        [{"status": 503, "json": {}}, {"status": 200, "text": "# Handbook"}],
    )

    reader.get_readme("handbook")

    assert len(calls) == 2


def test_retries_are_bounded_and_then_raise(monkeypatch):
    reader, calls = _reader(
        monkeypatch,
        [{"status": 503, "json": {}} for _ in range(10)],
        settings=GitHubLiveSettings(max_attempts=3),
    )

    with pytest.raises(SourceError):
        reader.get_readme("handbook")

    assert len(calls) == 3, "must not retry forever"


def test_client_error_is_not_retried(monkeypatch):
    """A 404 is terminal — retrying it just wastes the request budget."""
    reader, calls = _reader(
        monkeypatch, [{"status": 404, "json": {"message": "Not Found"}}]
    )

    with pytest.raises(SourceError):
        reader.get_readme("handbook")

    assert len(calls) == 1


def test_transport_error_is_wrapped_as_source_error(monkeypatch):
    import httpx

    def _boom(url, headers=None, params=None, timeout=None):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr("app.githublive.rest.httpx.get", _boom)
    reader = RestGitHubReader(
        token="ghs_tok", scope=_scope(), settings=GitHubLiveSettings(max_attempts=1)
    )

    with pytest.raises(SourceError):
        reader.get_readme("handbook")


# -- list_repos (no network: served from stored scope) ---------------------


def test_list_repos_comes_from_stored_scope_without_any_http_call(monkeypatch):
    reader, calls = _reader(monkeypatch, [])

    repos = reader.list_repos()

    assert {r.full_name for r in repos} == {"acme-inc/handbook", "acme-inc/payments-svc"}
    assert calls == []


# -- state is applied to the REQUEST, never to the result ------------------


def _pull_payload(number, *, merged=False, closed=False, title="Harden auth"):
    return {
        "number": number,
        "title": title,
        "user": {"login": "ada"},
        "state": "closed" if (merged or closed) else "open",
        "created_at": "2026-08-01T10:00:00Z",
        "updated_at": "2026-09-01T10:00:00Z",
        "merged_at": "2026-09-02T10:00:00Z" if merged else None,
        "closed_at": "2026-09-02T10:00:00Z" if (merged or closed) else None,
        "html_url": f"https://github.com/acme-inc/handbook/pull/{number}",
    }


def test_open_pull_requests_are_filtered_by_the_request_not_the_result(monkeypatch):
    """The bug this pins: filtering a newest-first window of 20 for `open`
    returns NOTHING on a busy repo, because the newest 20 are usually all
    merged -- and the agent then reports "no open pull requests" while several
    are open. GitHub can filter this server-side, so it must."""
    reader, calls = _reader(
        monkeypatch,
        [{"status": 200, "json": [_pull_payload(9)]}],
    )
    page = reader.list_pull_requests("handbook", limit=20, state="open")

    assert calls[0]["params"]["state"] == "open"
    assert [p.number for p in page.items] == [9]


def test_merged_asks_for_closed_and_drops_the_abandoned_ones(monkeypatch):
    """"merged" is not a GitHub state -- a merged pull request is `closed`, so
    counting off the request alone would count every abandoned branch."""
    reader, calls = _reader(
        monkeypatch,
        [{"status": 200, "json": [
            _pull_payload(1, merged=True),
            _pull_payload(2, closed=True),  # closed without merging
        ]}],
    )
    page = reader.list_pull_requests("handbook", limit=20, state="merged")

    assert calls[0]["params"]["state"] == "closed"
    assert [p.number for p in page.items] == [1]
    assert page.items[0].state == "merged"


def test_an_unrecognised_state_falls_back_to_all(monkeypatch):
    reader, calls = _reader(monkeypatch, [{"status": 200, "json": []}])
    reader.list_pull_requests("handbook", state="whatever")
    assert calls[0]["params"]["state"] == "all"


def test_get_pull_request_reads_the_merger_the_listing_omits(monkeypatch):
    """GitHub returns `merged_by` ONLY from the single-pull-request endpoint,
    which is why a chart of mergers cannot be built from a listing."""
    payload = _pull_payload(42, merged=True) | {"merged_by": {"login": "grace"}}
    reader, calls = _reader(monkeypatch, [{"status": 200, "json": payload}])

    pull = reader.get_pull_request("handbook", 42)

    assert pull.merged_by == "grace"
    assert pull.state == "merged"
    assert calls[0]["url"].endswith("/repos/acme-inc/handbook/pulls/42")
