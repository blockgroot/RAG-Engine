"""Prompt-Driven Activity Scheduler, Phase 2: per-service activity fetchers.

No real network, no real tokens, no DB: the Slack/GitHub HTTP layers are
faked so these assert the two things that actually matter —
(1) the caller's ``since`` reaches the outgoing request (a fetcher that
silently ignored it would report the same window forever), and
(2) the digest is plain text an LLM prompt can consume, empty when nothing
happened.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.exceptions import ConfigurationError, SourceError
from app.githublive.base import CommitSummary
from app.githublive.repos import InstallationScope, RepoRef
from app.schedulers import activity
from app.sources.linear import LinearAdapter
from app.sources.slack import SlackAdapter

SINCE = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# Slack
# --------------------------------------------------------------------------


class _FakeSlack(SlackAdapter):
    """SlackAdapter with its HTTP layer replaced, recording every call."""

    def __init__(self, pages: dict[str, list[dict]]):
        super().__init__(token="xoxb-fake", channel_ids=list(pages))
        self._pages = pages
        self.calls: list[dict] = []

    def _get(self, method: str, params: dict) -> dict:  # type: ignore[override]
        self.calls.append({"method": method, **params})
        if method == "conversations.history":
            return {"ok": True, "messages": self._pages[params["channel"]]}
        raise AssertionError(f"unexpected Slack call: {method}")

    def _display_name(self, user_id):  # type: ignore[override]
        return user_id or "unknown"

    def _channel_label(self, channel_id):  # type: ignore[override]
        return channel_id


def _message(ts: str, text: str, **extra) -> dict:
    return {"ts": ts, "text": text, "user": "U1", **extra}


def test_slack_fetch_passes_the_callers_since_to_the_api():
    adapter = _FakeSlack({"C1": [_message("1750000000.0", "shipped the thing")]})

    adapter.fetch_recent_messages(SINCE.timestamp())

    assert adapter.calls[0]["method"] == "conversations.history"
    assert adapter.calls[0]["oldest"] == SINCE.timestamp()


def test_slack_fetch_skips_system_events_and_empty_posts():
    adapter = _FakeSlack(
        {
            "C1": [
                _message("1.0", "real message"),
                _message("2.0", "joined", subtype="channel_join"),
                _message("3.0", "   "),
            ]
        }
    )

    messages = adapter.fetch_recent_messages(SINCE.timestamp())

    assert [m["text"] for m in messages] == ["real message"]


def test_slack_fetch_is_bounded_across_channels():
    adapter = _FakeSlack(
        {
            "C1": [_message(f"{i}.0", f"m{i}") for i in range(10)],
            "C2": [_message(f"{i}.0", f"n{i}") for i in range(10)],
        }
    )

    assert len(adapter.fetch_recent_messages(SINCE.timestamp(), max_messages=5)) == 5


def test_slack_activity_digest_is_plain_text(monkeypatch):
    adapter = _FakeSlack({"C1": [_message("1750000000.0", "deployed v2", reply_count=3)]})
    _patch_slack_wiring(monkeypatch, adapter)

    text = activity.fetch_slack_activity("org-1", SINCE)

    assert "deployed v2" in text
    assert "#C1" in text
    assert "3 replies" in text


def test_slack_activity_is_empty_when_nothing_happened(monkeypatch):
    _patch_slack_wiring(monkeypatch, _FakeSlack({"C1": []}))

    assert activity.fetch_slack_activity("org-1", SINCE) == ""


def _patch_slack_wiring(monkeypatch, adapter):
    """Stub the credential + adapter lookups so no DB or Slack token is needed."""
    monkeypatch.setattr(
        "app.auth.credentials.get_connection_config",
        lambda *a, **k: {"channel_ids": ["C1"]},
    )
    monkeypatch.setattr(
        "app.auth.credentials.get_live_connection_token", lambda *a, **k: "xoxb-fake"
    )
    monkeypatch.setattr("app.sources.build_source_adapter", lambda *a, **k: adapter)


def test_slack_activity_fails_loudly_when_slack_is_not_connected(monkeypatch):
    monkeypatch.setattr(
        "app.auth.credentials.get_connection_config", lambda *a, **k: None
    )
    with pytest.raises(ConfigurationError):
        activity.fetch_slack_activity("org-1", SINCE)


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------


class _FakeReader:
    """Records the `since` it was asked for; returns canned commits per repo."""

    def __init__(self, commits: dict[str, list[CommitSummary]], fail: set[str] = frozenset()):
        self._commits = commits
        self._fail = fail
        self.calls: list[tuple[str, str | None]] = []

    def list_commits(self, repo, *, path=None, since=None, limit=10):
        self.calls.append((repo, since))
        if repo in self._fail:
            raise SourceError(f"boom for {repo}")
        return self._commits.get(repo, [])[:limit]


def _commit(repo: str, sha: str, message: str) -> CommitSummary:
    return CommitSummary(
        repo=repo,
        sha=sha,
        message=message,
        author="dev",
        date=SINCE + timedelta(days=1),
        url=f"https://github.com/{repo}/commit/{sha}",
    )


def _patch_github_wiring(monkeypatch, reader, repo_names):
    scope = InstallationScope(
        installation_id="1",
        account_login="acme",
        repository_selection="selected",
        repos=tuple(RepoRef(full_name=name) for name in repo_names),
    )
    monkeypatch.setattr("app.githublive.scope.load_scope", lambda *a, **k: scope)
    monkeypatch.setattr("app.githublive.build_github_reader", lambda *a, **k: reader)


def test_github_fetch_passes_since_and_formats_commits(monkeypatch):
    reader = _FakeReader({"acme/api": [_commit("acme/api", "abc1234def", "fix login")]})
    _patch_github_wiring(monkeypatch, reader, ["acme/api"])

    text = activity.fetch_github_activity("org-1", SINCE)

    assert reader.calls == [("acme/api", SINCE.isoformat())]
    assert "acme/api" in text
    assert "abc1234" in text  # short sha, not the full one
    assert "fix login" in text


def test_github_one_bad_repo_does_not_lose_the_others(monkeypatch):
    """A permission-changed repo must not cost the user every other repo."""
    reader = _FakeReader(
        {"acme/good": [_commit("acme/good", "1111111", "still works")]},
        fail={"acme/bad"},
    )
    _patch_github_wiring(monkeypatch, reader, ["acme/bad", "acme/good"])

    text = activity.fetch_github_activity("org-1", SINCE)

    assert "still works" in text
    assert "acme/bad" not in text


def test_github_activity_is_empty_when_no_commits(monkeypatch):
    _patch_github_wiring(monkeypatch, _FakeReader({}), ["acme/api"])

    assert activity.fetch_github_activity("org-1", SINCE) == ""


def test_github_marks_when_repo_list_was_truncated(monkeypatch):
    """The marker matters more than the cut — a partial check must say so."""
    names = [f"acme/repo{i}" for i in range(activity.MAX_REPOS + 5)]
    _patch_github_wiring(monkeypatch, _FakeReader({}), names)

    text = activity.fetch_github_activity("org-1", SINCE)

    assert f"first {activity.MAX_REPOS}" in text


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------


def test_dispatch_routes_to_the_right_fetcher(monkeypatch):
    monkeypatch.setattr(
        activity, "_FETCHERS", {"slack": lambda *a, **k: "slack digest"}
    )
    assert activity.fetch_activity("slack", "org-1", SINCE) == "slack digest"


def test_dispatch_raises_for_a_provider_with_no_fetcher():
    """Silently returning empty would look like 'nothing happened' forever."""
    with pytest.raises(ConfigurationError):
        activity.fetch_activity("notion", "org-1", SINCE)


# --------------------------------------------------------------------------
# Size bounds (counting entries is not enough — measured on real data)
# --------------------------------------------------------------------------


def test_one_huge_entry_is_clipped_and_marked():
    """Real Slack posts run to thousands of chars; one must not eat the budget."""
    clipped = activity._clip("x" * 10_000)

    assert len(clipped) < activity.MAX_ENTRY_CHARS + 20
    assert clipped.endswith("[…]")  # marked, so the model can't read it as whole


def test_entry_newlines_are_collapsed_so_one_entry_stays_one_line():
    assert "\n" not in activity._clip("line one\nline two\n\nline three")


def test_a_short_entry_is_untouched():
    assert activity._clip("- [2026-08-01] abc1234 fix login (by dev)") == (
        "- [2026-08-01] abc1234 fix login (by dev)"
    )


def test_the_digest_stops_at_the_char_budget_with_a_marker():
    """Three real Slack messages measured 6,637 chars — count alone can't bound this."""
    lines = [f"- entry {i} " + "y" * 1500 for i in range(200)]

    digest = activity._join_bounded(lines)

    assert len(digest) <= activity.MAX_DIGEST_CHARS + len(activity._TRUNCATION_MARKER) + 1
    assert activity._TRUNCATION_MARKER in digest
    assert "entry 0" in digest  # kept the earliest, dropped the tail


def test_a_normal_digest_is_never_truncated():
    """The bound must not fire on ordinary activity."""
    lines = [f"- [2026-08-0{i}] sha{i} did a thing (by dev)" for i in range(1, 9)]

    digest = activity._join_bounded(lines)

    assert activity._TRUNCATION_MARKER not in digest
    assert digest.count("\n") == 7


def test_slack_digest_applies_the_bounds(monkeypatch):
    adapter = _FakeSlack(
        {"C1": [_message(f"{i}.0", "z" * 5000) for i in range(50)]}
    )
    _patch_slack_wiring(monkeypatch, adapter)

    text = activity.fetch_slack_activity("org-1", SINCE)

    assert len(text) <= activity.MAX_DIGEST_CHARS + len(activity._TRUNCATION_MARKER) + 1


def test_github_digest_applies_the_bounds(monkeypatch):
    """Squashed commit bodies are routinely thousands of characters."""
    reader = _FakeReader(
        {"acme/api": [_commit("acme/api", f"sha{i:07d}", "m" * 5000) for i in range(40)]}
    )
    _patch_github_wiring(monkeypatch, reader, ["acme/api"])

    text = activity.fetch_github_activity("org-1", SINCE)

    assert len(text) <= activity.MAX_DIGEST_CHARS + len(activity._TRUNCATION_MARKER) + 1


# --------------------------------------------------------------------------
# Linear
# --------------------------------------------------------------------------


class _FakeLinear(LinearAdapter):
    """LinearAdapter with its GraphQL transport replaced, recording queries."""

    def __init__(self, pages: list[dict]):
        super().__init__(token="lin_api_fake", oauth=True)
        self._pages = pages
        self.calls: list[dict] = []

    def _query(self, query: str, variables: dict | None = None) -> dict:  # type: ignore[override]
        self.calls.append({"query": query, "variables": variables or {}})
        page = self._pages[min(len(self.calls) - 1, len(self._pages) - 1)]
        return {"issues": page}


def _issue(identifier: str, title: str, state="In Review", state_type="started", assignee="Priya"):
    return {
        "identifier": identifier,
        "title": title,
        "url": f"https://linear.app/acme/issue/{identifier}",
        "updatedAt": "2026-08-02T09:30:00.000Z",
        "state": {"name": state, "type": state_type},
        "assignee": {"name": assignee} if assignee else None,
    }


def _page(nodes, has_next=False, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": has_next, "endCursor": cursor}}


def test_linear_fetch_filters_server_side_on_updated_at():
    """The whole point: Linear supports this natively, the adapter never asked."""
    adapter = _FakeLinear([_page([_issue("ENG-1", "fix billing")])])

    adapter.fetch_recent_issues(SINCE)

    variables = adapter.calls[0]["variables"]
    assert variables["filter"] == {"updatedAt": {"gt": SINCE.isoformat()}}
    assert "IssueFilter" in adapter.calls[0]["query"]


def test_linear_fetch_returns_state_and_assignee_not_just_titles():
    """"What shipped" / "what's stuck" is unanswerable from titles alone."""
    adapter = _FakeLinear([_page([_issue("ENG-7", "ship checkout", "Done", "completed", "Sam")])])

    issues = adapter.fetch_recent_issues(SINCE)

    assert issues[0]["identifier"] == "ENG-7"
    assert issues[0]["state"] == "Done"
    assert issues[0]["state_type"] == "completed"
    assert issues[0]["assignee"] == "Sam"
    assert issues[0]["at"] is not None


def test_linear_fetch_handles_an_unassigned_issue():
    """`assignee` is null in Linear's response, not an empty object."""
    adapter = _FakeLinear([_page([_issue("ENG-9", "triage", assignee=None)])])

    assert adapter.fetch_recent_issues(SINCE)[0]["assignee"] == ""


def test_linear_fetch_follows_pagination_but_stops_at_the_cap():
    pages = [_page([_issue(f"ENG-{i}", f"t{i}") for i in range(5)], True, "c1")] * 10
    adapter = _FakeLinear(pages)

    issues = adapter.fetch_recent_issues(SINCE, max_issues=12)

    assert len(issues) == 12


def test_linear_fetch_stops_when_there_is_no_next_page():
    adapter = _FakeLinear([_page([_issue("ENG-1", "only one")], has_next=False)])

    assert len(adapter.fetch_recent_issues(SINCE, max_issues=300)) == 1
    assert len(adapter.calls) == 1  # did not keep paginating


def test_linear_activity_digest_names_the_issue_state_and_owner(monkeypatch):
    adapter = _FakeLinear([_page([_issue("ENG-42", "migrate payments", "Done", "completed", "Ada")])])
    monkeypatch.setattr(
        "app.auth.credentials.get_live_connection_token", lambda *a, **k: "lin_fake"
    )
    monkeypatch.setattr("app.auth.credentials.get_connection_config", lambda *a, **k: {})
    monkeypatch.setattr("app.sources.build_source_adapter", lambda *a, **k: adapter)

    text = activity.fetch_linear_activity("org-1", SINCE)

    assert "ENG-42" in text
    assert "migrate payments" in text
    assert "Done" in text
    assert "completed" in text
    assert "Ada" in text


def test_linear_activity_uses_the_oauth_credential_path(monkeypatch):
    """A scheduler is created against an oauth_connections row, never an env key.

    Passing `token=` is also what makes the adapter send `Bearer <token>` —
    get this wrong and every request 401s while looking authenticated.
    """
    seen: dict = {}
    monkeypatch.setattr(
        "app.auth.credentials.get_live_connection_token", lambda *a, **k: "lin_oauth"
    )
    monkeypatch.setattr("app.auth.credentials.get_connection_config", lambda *a, **k: {})

    def _build(source_type, **kwargs):
        seen.update({"source_type": source_type, **kwargs})
        return _FakeLinear([_page([])])

    monkeypatch.setattr("app.sources.build_source_adapter", _build)

    activity.fetch_linear_activity("org-1", SINCE)

    assert seen["source_type"] == "linear"
    assert seen["token"] == "lin_oauth"  # not a token_name env lookup


def test_linear_activity_is_empty_when_nothing_moved(monkeypatch):
    monkeypatch.setattr(
        "app.auth.credentials.get_live_connection_token", lambda *a, **k: "lin_fake"
    )
    monkeypatch.setattr("app.auth.credentials.get_connection_config", lambda *a, **k: {})
    monkeypatch.setattr(
        "app.sources.build_source_adapter", lambda *a, **k: _FakeLinear([_page([])])
    )

    assert activity.fetch_linear_activity("org-1", SINCE) == ""


def test_linear_digest_applies_the_size_bounds(monkeypatch):
    adapter = _FakeLinear([_page([_issue(f"ENG-{i}", "q" * 5000) for i in range(60)])])
    monkeypatch.setattr(
        "app.auth.credentials.get_live_connection_token", lambda *a, **k: "lin_fake"
    )
    monkeypatch.setattr("app.auth.credentials.get_connection_config", lambda *a, **k: {})
    monkeypatch.setattr("app.sources.build_source_adapter", lambda *a, **k: adapter)

    text = activity.fetch_linear_activity("org-1", SINCE)

    assert len(text) <= activity.MAX_DIGEST_CHARS + len(activity._TRUNCATION_MARKER) + 1


# --------------------------------------------------------------------------
# The store/fetcher invariant
# --------------------------------------------------------------------------


def test_every_supported_provider_has_a_fetcher():
    """A provider offered by the API but missing a fetcher would create
    schedulers that fail every single cycle — and vice versa, a fetcher no
    provider can select is dead code. Pin them together."""
    from app.schedulers.store import SUPPORTED_PROVIDERS

    assert set(SUPPORTED_PROVIDERS) == set(activity._FETCHERS)
