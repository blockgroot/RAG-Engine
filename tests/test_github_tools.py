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


def test_a_merge_with_no_named_merger_says_unknown_rather_than_guessing():
    page = PullRequestPage(items=(_pr(3, merged_by=None, merged=True),))
    text, _ = GitHubAgent._format_pull_requests(page)
    assert "unknown" in text.lower()


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
