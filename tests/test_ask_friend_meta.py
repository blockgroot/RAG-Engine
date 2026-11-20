"""A refused turn is marked so Ask a friend can be offered (also on reopen), and
it is sent to ONE colleague: the claim is a conditional UPDATE, so a double
click or a second tab cannot send twice."""

from __future__ import annotations

import pytest

from app.memory.conversations import (
    claim_ask_friend, meta_for_history, meta_for_storage, release_ask_friend,
)
from .ask_friend_support import add_turn, make_conversation, make_org, make_user, turn_meta
from .conftest import requires_db


def test_meta_keeps_ask_friend_flags():
    assert meta_for_storage({"source": "notion", "ask_friend": True})["ask_friend"] is True
    stored = {"source": "notion", "ask_friend": True,
              "ask_friend_sent": {"to_name": "Priya Shah", "via": "slack", "at": "t"}}
    assert meta_for_history(stored)["ask_friend_sent"]["via"] == "slack"


@pytest.fixture
def chat(org_cleanup):
    org = make_org()
    org_cleanup.append(org)
    a, b = make_user(org), make_user(org)
    cid = make_conversation(org, a)
    add_turn(cid, org, "q?", "I couldn't find that.", meta={"source": "none", "ask_friend": True})
    return org, a, b, cid


@requires_db
def test_claim_is_once_and_owner_only(chat):
    org, a, b, cid = chat
    assert not claim_ask_friend(conversation_id=cid, org_id=org, user_id=b, question="q?", entry={"via": "email"})
    assert claim_ask_friend(conversation_id=cid, org_id=org, user_id=a, question="q?",
                            entry={"to_name": "Priya Shah", "via": "email", "at": "t1"})
    assert turn_meta(cid)["ask_friend_sent"]["to_name"] == "Priya Shah"
    assert not claim_ask_friend(conversation_id=cid, org_id=org, user_id=a, question="q?", entry={"via": "slack"})
    release_ask_friend(conversation_id=cid, org_id=org, user_id=a, question="q?")
    assert "ask_friend_sent" not in turn_meta(cid)
    assert claim_ask_friend(conversation_id=cid, org_id=org, user_id=a, question="q?", entry={"via": "slack"})


@requires_db
def test_claim_refuses_a_turn_that_was_answered(org_cleanup):
    org = make_org()
    org_cleanup.append(org)
    a = make_user(org)
    cid = make_conversation(org, a)
    add_turn(cid, org, "q?", "Leave is 4 weeks.", meta={"source": "notion"})
    assert not claim_ask_friend(conversation_id=cid, org_id=org, user_id=a, question="q?", entry={})
