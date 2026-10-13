"""Feedback rows are a tenant read, so the same isolation rule applies as
everywhere else: ``org_id`` is a WHERE clause, and a missing predicate LEAKS
rather than fails -- the gap list still renders, just with another company's
unanswered questions in it.

The other thing pinned here is that a downvote ON a refusal updates that one
row rather than adding a second. Getting that wrong double-counts every gap
somebody bothered to report, which would make the one number an admin acts on
("nine people asked this") quietly wrong in the direction of looking worse.
"""

from __future__ import annotations

import uuid

import pytest

from app.db import get_connection
from app import feedback
from .conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"feedback-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _user(org_id: str) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO users (email, org_id, role) VALUES (%s, %s, 'member') "
            "RETURNING id",
            (f"{uuid.uuid4().hex[:8]}@example.com", org_id),
        ).fetchone()
        conn.commit()
    return str(row[0])


def _conversation(org_id: str, user_id: str) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO conversations (org_id, user_id) VALUES (%s, %s) RETURNING id",
            (org_id, user_id),
        ).fetchone()
        conn.commit()
    return str(row[0])


def test_normalize_groups_the_same_question_asked_differently():
    assert feedback.normalize_question("  How much LEAVE do I have?? ") == (
        feedback.normalize_question("how much leave do i have")
    )


def test_refusal_is_logged_and_surfaces_as_a_gap(org):
    user = _user(org)
    feedback.record_gap(
        org_id=org,
        question="What is the parental leave policy?",
        user_id=user,
        agent="notion",
        gate_score=0.21,
    )

    gaps = feedback.list_gaps(org_id=org)
    assert len(gaps) == 1
    assert gaps[0].refusals == 1
    assert gaps[0].asked_by == 1
    assert gaps[0].best_gate_score == pytest.approx(0.21, abs=1e-4)


def test_gap_counts_people_not_repeats(org):
    """One person asking three times is one person, not three.

    This is the number the dashboard leads with, so it is the one that must
    not flatter the problem.
    """
    one, two = _user(org), _user(org)
    for who in (one, one, one, two):
        feedback.record_gap(
            org_id=org, question="health insurance reimbursement", user_id=who
        )

    gap = feedback.list_gaps(org_id=org)[0]
    assert gap.asked_by == 2
    assert gap.refusals == 4


def test_downvote_on_a_refusal_updates_that_row(org):
    """A refusal plus a thumbs-down on it is ONE event."""
    user = _user(org)
    conv = _conversation(org, user)
    feedback.record_gap(
        org_id=org,
        question="Where is the expenses policy?",
        user_id=user,
        conversation_id=conv,
    )
    feedback.record_rating(
        org_id=org,
        user_id=user,
        conversation_id=conv,
        question="Where is the expenses policy?",
        rating=-1,
        reason="out_of_date",
        comment="the policy changed last month",
    )

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT refusal, rating, reason FROM feedback_and_gaps WHERE org_id = %s",
            (org,),
        ).fetchall()
    assert rows == [(True, -1, "out_of_date")]

    gap = feedback.list_gaps(org_id=org)[0]
    assert (gap.refusals, gap.downvotes) == (1, 1)


def test_re_clicking_a_thumb_corrects_it(org):
    user = _user(org)
    conv = _conversation(org, user)
    first = feedback.record_rating(
        org_id=org,
        user_id=user,
        conversation_id=conv,
        question="who merged the auth PR?",
        rating=-1,
    )
    second = feedback.record_rating(
        org_id=org,
        user_id=user,
        conversation_id=conv,
        question="Who merged the auth PR??",
        rating=1,
    )
    assert first == second

    counts = feedback.rating_counts(org_id=org)
    assert (counts["up"], counts["down"]) == (1, 0)


def test_an_upvote_is_not_a_gap(org):
    """A rated-good answer must never appear in the list of missing documents."""
    user = _user(org)
    conv = _conversation(org, user)
    feedback.record_rating(
        org_id=org,
        user_id=user,
        conversation_id=conv,
        question="how do I book leave?",
        rating=1,
    )
    assert feedback.list_gaps(org_id=org) == []
    assert feedback.rating_counts(org_id=org)["up"] == 1


def test_reads_are_scoped_to_one_org(org, org_cleanup):
    """The predicate that keeps two companies apart, pinned in both directions."""
    with get_connection() as conn:
        other = str(
            conn.execute(
                "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
                (f"feedback-other-{uuid.uuid4().hex[:8]}",),
            ).fetchone()[0]
        )
        conn.commit()
    org_cleanup.append(other)

    feedback.record_gap(org_id=org, question="ours", user_id=_user(org))
    feedback.record_gap(org_id=other, question="theirs", user_id=_user(other))

    assert [g.question for g in feedback.list_gaps(org_id=org)] == ["ours"]
    assert [g.question for g in feedback.list_gaps(org_id=other)] == ["theirs"]


def test_a_bad_reason_is_refused(org):
    user = _user(org)
    conv = _conversation(org, user)
    with pytest.raises(ValueError):
        feedback.record_rating(
            org_id=org,
            user_id=user,
            conversation_id=conv,
            question="anything",
            rating=-1,
            reason="because-i-said-so",
        )


def test_a_blank_question_is_not_a_gap(org):
    feedback.record_gap(org_id=org, question="   ", user_id=_user(org))
    assert feedback.list_gaps(org_id=org) == []


def test_a_refused_chart_is_listed_with_why_and_not_as_a_missing_document(org):
    """A chart nothing could draw is not a document nobody wrote: it has its
    own list, and the latest refusal's heading says what to fix."""
    one, two = _user(org), _user(org)
    for who, answer in ((one, "**This can't be shown as a chart**\nTopics in text."),
                        (two, "**Slack is not connected here**\nAn admin can connect it.")):
        feedback.record_gap(org_id=org, question="Slack messages per person",
                            user_id=who, agent="insights", answer=answer)
    feedback.record_gap(org_id=org, question="parental leave policy", user_id=one,
                        agent="notion")

    assert [g.question for g in feedback.list_gaps(org_id=org)] == ["parental leave policy"]
    [chart] = feedback.list_chart_gaps(org_id=org)
    assert (chart.question, chart.asked_by, chart.times) == ("Slack messages per person", 2, 2)
    assert chart.why == "Slack is not connected here"


def test_chart_gaps_are_per_company(org, org_cleanup):
    with get_connection() as conn:
        other = str(conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"feedback-{uuid.uuid4().hex[:8]}",),
        ).fetchone()[0])
        conn.commit()
    org_cleanup.append(other)
    feedback.record_gap(org_id=other, question="theirs", agent="insights", answer="no")
    assert feedback.list_chart_gaps(org_id=org) == []
