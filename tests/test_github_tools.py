"""The GitHub agent's tool surface.

GitHub stores no chunks, so this tool list IS the whole set of questions it can
answer -- anything absent degrades to the fixed fallback. Pull requests and
reviews existed on the reader for MONTHS (added for charts) before they were
offered here, which made "who reviewed the auth PR?" unanswerable against data
that was one call away. These tests exist so that cannot happen silently again.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.agent.github_agent import GitHubAgent
from app.githublive.base import Branch, PullRequest, PullRequestPage, Review
from app.rag.prompts import GITHUB_TOOLS


def _pr(number, *, author="ada", merged_by=None, merged=False, title="Harden auth"):
    now = datetime.now(timezone.utc)
    return PullRequest(
        repo="acme/api",
        number=number,
        title=title,
        author=author,
        merged_by=merged_by,
        state="merged" if merged else "open",
        created_at=now - timedelta(days=5),
        merged_at=now - timedelta(days=1) if merged else None,
        closed_at=now - timedelta(days=1) if merged else None,
        url=f"https://github.com/acme/api/pull/{number}",
    )


# --------------------------------------------------------------------------
# The surface itself
# --------------------------------------------------------------------------


def test_every_reader_capability_is_offered_as_a_tool():
    """The regression that motivated this file: a reader method the agent was
    never given is data the product cannot reach."""
    offered = {t["function"]["name"] for t in GITHUB_TOOLS}
    assert offered == {
        "get_readme",
        "get_commit",
        "list_commits",
        "list_pull_requests",
        "list_reviews",
        "list_branches",
    }


def test_every_tool_requires_a_repo():
    """`resolve_repo` is what stops a coaxed foreign repo name being read, and
    it can only run if a repo was named."""
    for tool in GITHUB_TOOLS:
        assert "repo" in tool["function"]["parameters"]["required"]


# --------------------------------------------------------------------------
# Pull requests -- three distinct people
# --------------------------------------------------------------------------


def test_pull_requests_name_the_author_and_the_merger_separately():
    """Who raised it and who merged it are different claims. Collapsing them
    produces "ada did 12 things", which nobody asked for."""
    page = PullRequestPage(
        items=(_pr(1, author="ada", merged_by="grace", merged=True),)
    )
    text, citations = GitHubAgent._format_pull_requests(page)
    assert "by ada" in text
    assert "merged" in text and "by grace" in text
    assert citations[0].reference == "acme/api#1"


def test_an_unmerged_pull_request_is_not_described_as_merged():
    page = PullRequestPage(items=(_pr(2, merged=False),))
    text, _ = GitHubAgent._format_pull_requests(page)
    assert "merged" not in text.lower()
    assert "open" in text


def test_a_merge_with_no_named_merger_names_nobody_at_all():
    """Absent is OMITTED, not "unknown".

    GitHub leaves `merged_by` out of the pulls LIST payload entirely, so
    "merged by unknown" would appear on nearly every merged pull request and
    read as a claim about the merger rather than as a gap in what we read.
    """
    pull = _pr(3, merged_by=None, merged=True)
    text, _ = GitHubAgent._format_pull_requests(PullRequestPage(items=(pull,)))
    merged = f"merged {pull.merged_at.date().isoformat()}"

    assert merged in text
    assert "unknown" not in text.lower()
    assert " by " in text.split(merged)[0]  # the AUTHOR still shows


def test_the_state_filter_narrows_without_a_second_api_call():
    page = PullRequestPage(items=(
        _pr(1, merged=True, merged_by="grace"),
        _pr(2, merged=False),
    ))
    merged, _ = GitHubAgent._format_pull_requests(page, state="merged")
    assert "#1" in merged and "#2" not in merged

    opened, _ = GitHubAgent._format_pull_requests(page, state="open")
    assert "#2" in opened and "#1" not in opened


def test_truncation_is_stated_to_the_model_not_left_implicit():
    """A list built from the newest N while looking complete is the failure
    that matters."""
    page = PullRequestPage(items=(_pr(1),), truncated=True)
    text, _ = GitHubAgent._format_pull_requests(page)
    assert "there may be more" in text.lower()


def test_no_pull_requests_falls_back_rather_than_narrating_an_empty_list():
    assert GitHubAgent._format_pull_requests(PullRequestPage()) is None


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------


def test_reviews_are_deduplicated_per_person():
    """Someone who comments four times reviewed ONE pull request. Listing
    every event would make a chatty reviewer look like four people."""
    reviews = [
        Review(repo="acme/api", pull_number=7, reviewer="grace",
               state="COMMENTED", submitted_at=None),
        Review(repo="acme/api", pull_number=7, reviewer="grace",
               state="APPROVED", submitted_at=None),
        Review(repo="acme/api", pull_number=7, reviewer="linus",
               state="CHANGES_REQUESTED", submitted_at=None),
    ]
    text, _ = GitHubAgent._format_reviews("acme/api", 7, reviews)
    assert text.count("grace") == 1
    assert "linus" in text
    assert "changes requested" in text


def test_an_unreviewed_pull_request_answers_rather_than_falling_back():
    """Unlike an empty commit list, "nobody has reviewed it" IS the information
    the asker wanted -- falling back would hide a real answer behind the fixed
    "I don't know"."""
    result = GitHubAgent._format_reviews("acme/api", 9, [])
    assert result is not None
    text, _ = result
    assert "no reviews" in text.lower()
    assert "#9" in text


# --------------------------------------------------------------------------
# Branches
# --------------------------------------------------------------------------


def test_branches_report_protection_rather_than_guessing_it():
    branches = [
        Branch(repo="acme/api", name="main", protected=True),
        Branch(repo="acme/api", name="feat/audit", protected=False),
    ]
    text, _ = GitHubAgent._format_branches(branches)
    assert "main (protected)" in text
    assert "feat/audit" in text
    assert "feat/audit (protected)" not in text


def test_no_branches_falls_back():
    assert GitHubAgent._format_branches([]) is None


# --------------------------------------------------------------------------
# Routing reachability
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "who raised the most pull requests?",
        "which branches are active?",
        "who are the reviewers on the api repo?",
        "who approved that change?",
        "what commits landed last week?",
    ],
)
def test_code_shaped_questions_reach_github_by_intent(question):
    """GitHub embeds nothing, so it can never win the cosine probe -- these
    keyword rules are the only way it is reachable at all."""
    from app.agent.routing import _CODE_INTENT

    assert _CODE_INTENT.search(question), question


@pytest.mark.parametrize(
    "question",
    [
        "what is the leave policy?",
        "how many days of holiday do I get?",
        "what did we decide about pricing?",
    ],
)
def test_ordinary_document_questions_do_not_look_like_code(question):
    """Code intent is checked LAST, but a document question matching it would
    still be a needless risk."""
    from app.agent.routing import _CODE_INTENT

    assert not _CODE_INTENT.search(question), question


# --------------------------------------------------------------------------
# Review order: the verdict, not the first event
# --------------------------------------------------------------------------


def _review(who, state, *, minutes_ago):
    return Review(
        repo="acme/api",
        pull_number=7,
        reviewer=who,
        state=state,
        submitted_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


def test_a_comment_then_an_approval_is_reported_as_the_approval():
    """GitHub returns reviews OLDEST-first.

    "Commented, then approved" is the most ordinary review sequence there is.
    Keeping the first event stored COMMENTED, so "who approved #7?" missed the
    person who actually approved it.
    """
    reviews = [
        _review("grace", "COMMENTED", minutes_ago=90),
        _review("grace", "APPROVED", minutes_ago=10),
    ]
    text, _ = GitHubAgent._format_reviews("acme/api", 7, reviews)

    assert text.count("grace") == 1  # still one person, not two events
    assert "approved" in text
    assert "commented" not in text


def test_an_approval_survives_later_chatter():
    """A verdict outranks a comment even when the comment came last -- a
    follow-up remark does not withdraw an approval."""
    reviews = [
        _review("grace", "APPROVED", minutes_ago=90),
        _review("grace", "COMMENTED", minutes_ago=10),
    ]
    text, _ = GitHubAgent._format_reviews("acme/api", 7, reviews)
    assert "approved" in text
    assert "commented" not in text


def test_changes_requested_is_a_verdict_too():
    reviews = [
        _review("ada", "COMMENTED", minutes_ago=60),
        _review("ada", "CHANGES_REQUESTED", minutes_ago=5),
    ]
    text, _ = GitHubAgent._format_reviews("acme/api", 7, reviews)
    assert "changes requested" in text


# --------------------------------------------------------------------------
# "who reviewed the auth PR?" -- reachable in ONE tool round
# --------------------------------------------------------------------------


class _ReviewReader:
    """Enough reader to exercise the number resolution, and nothing else."""

    def __init__(self, pulls):
        self._pulls = pulls
        self.reviewed: list[int] = []

    def list_pull_requests(self, repo, *, since=None, limit=100, state="all"):
        return PullRequestPage(items=tuple(self._pulls))

    def list_reviews(self, repo, pull_number):
        self.reviewed.append(pull_number)
        return [_review("grace", "APPROVED", minutes_ago=5)]


def _resolve(reader, arguments):
    from app.agent.github_agent import _resolve_pull_number

    return _resolve_pull_number(reader, "acme/api", arguments)


def test_a_review_question_resolves_the_pull_request_by_title():
    """The agent runs ONE tool round and never loops, so a model that had to
    know the number first could never reach reviews for "who reviewed the auth
    PR?" -- the tool would exist and be unreachable."""
    reader = _ReviewReader([
        _pr(11, title="Bump dependencies"),
        _pr(12, title="Harden auth token refresh"),
    ])
    assert _resolve(reader, {"pull_query": "auth"}) == 12


def test_an_explicit_number_is_used_without_a_lookup():
    reader = _ReviewReader([_pr(11, title="Bump dependencies")])
    assert _resolve(reader, {"pull_number": 142}) == 142


def test_two_equally_good_titles_resolve_to_nothing():
    """Answering about the WRONG pull request is worse than falling back."""
    reader = _ReviewReader([
        _pr(11, title="Auth rewrite"),
        _pr(12, title="Auth cleanup"),
    ])
    assert _resolve(reader, {"pull_query": "auth"}) is None


def test_no_number_and_no_query_is_a_fallback_not_a_guess():
    reader = _ReviewReader([_pr(11, title="Harden auth")])
    assert _resolve(reader, {}) is None


# --------------------------------------------------------------------------
# The merger budget is on CALLS, not on list size
# --------------------------------------------------------------------------


class _DetailReader:
    def __init__(self, merger="grace"):
        self.calls: list[int] = []
        self._merger = merger

    def get_pull_request(self, repo, number):
        self.calls.append(number)
        return _pr(number, merged_by=self._merger, merged=True)


def test_the_default_browse_of_twenty_still_names_recent_mergers():
    """Bounding by LIST SIZE meant a default list of 20 was skipped whole, so
    Ask could never name a merger -- the one question the field is for."""
    from app.agent.github_agent import _MERGER_DETAIL_CALLS, _with_mergers

    page = PullRequestPage(
        items=tuple(_pr(n, merged_by=None, merged=True) for n in range(20))
    )
    reader = _DetailReader()
    filled = _with_mergers(reader, page)

    assert len(reader.calls) == _MERGER_DETAIL_CALLS
    # Newest-first, so the budget lands on the most recent merges.
    assert [p.merged_by for p in filled.items[:_MERGER_DETAIL_CALLS]] == (
        ["grace"] * _MERGER_DETAIL_CALLS
    )
    assert filled.items[_MERGER_DETAIL_CALLS].merged_by is None


def test_open_pull_requests_cost_no_detail_calls():
    from app.agent.github_agent import _with_mergers

    reader = _DetailReader()
    _with_mergers(reader, PullRequestPage(items=(_pr(1), _pr(2))))
    assert reader.calls == []


def test_a_failed_detail_call_spends_its_budget_and_keeps_the_row():
    """A retry loop against a rate limit is worse than an omitted name."""
    from app.agent.github_agent import _with_mergers

    class _Broken(_DetailReader):
        def get_pull_request(self, repo, number):
            self.calls.append(number)
            raise ValueError("502")

    reader = _Broken()
    page = PullRequestPage(
        items=tuple(_pr(n, merged_by=None, merged=True) for n in range(10))
    )
    filled = _with_mergers(reader, page)

    assert len(reader.calls) == 5
    assert len(filled.items) == 10
    assert all(p.merged_by is None for p in filled.items)
