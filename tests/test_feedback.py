"""How refusals and thumbs are written. Grouping them for the people who fix
them, and keeping companies apart there, is ``test_gap_topics.py``.

The thing pinned here is that a downvote ON a refusal updates that one
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


def _rows(org_id: str, columns: str) -> list[tuple]:
    with get_connection() as conn:
        return conn.execute(
            f"SELECT {columns} FROM feedback_and_gaps WHERE org_id = %s", (org_id,)
        ).fetchall()


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

    [(refusal, who, score)] = _rows(org, "refusal, user_id::text, gate_score")
    assert (refusal, who) == (True, user)
    assert score == pytest.approx(0.21, abs=1e-4)


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

    assert _rows(org, "rating") == [(1,)]


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
    assert _rows(org, "id") == []


