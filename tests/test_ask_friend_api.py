"""The Ask a friend routes: only the asker's own refused turn, only a member of
the chat's scope, one colleague per turn, private content only with the tick,
and a failed delivery can be sent again."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.askfriend import service
from app.askfriend.deliver import DeliveryError
from app.auth import create_admin, create_session_token
from app.workspaces.store import create_workspace
from .ask_friend_support import add_turn, make_conversation, make_org, make_user, turn_meta
from .conftest import requires_db

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("EMAIL_SENDER", "console")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


@pytest.fixture
def world(org_cleanup, monkeypatch):
    """Asker A (with a refused turn), colleague B in the same company, and fakes
    for the model and delivery that record what was sent."""
    org = make_org()
    org_cleanup.append(org)
    asker = create_admin(f"a-{uuid.uuid4().hex[:8]}@example.com", org)
    b = make_user(org, name="Priya Shah")
    cid = make_conversation(org, asker.id)
    add_turn(cid, org, "What does bench-sandbox do?", "I couldn't find that.",
             meta={"source": "github", "ask_friend": True})
    sent: list[tuple] = []
    monkeypatch.setattr(service, "_summary", lambda turns, question, model, session: "S")
    monkeypatch.setattr(service, "slack_token", lambda org_id, ws: "xoxb")
    monkeypatch.setattr(service, "lookup_user_id", lambda token, email: "U1")
    monkeypatch.setattr(service, "send_slack", lambda *a: sent.append(("slack", *a)))
    monkeypatch.setattr(service, "send_email", lambda *a: sent.append(("email", *a)))
    return {"org": org, "asker": asker, "b": b, "cid": cid, "sent": sent,
            "cookies": {"session": create_session_token(asker)}}


def _body(w, **over):
    body = {"conversation_id": w["cid"], "question": "What does bench-sandbox do?",
            "recipient_user_id": w["b"], "via": "slack", "approve_private": False}
    body.update(over)
    return body


def test_people_lists_scope_members_without_asker(client, world):
    r = client.get("/ask-friend/people", cookies=world["cookies"])
    assert r.status_code == 200
    assert [p["name"] for p in r.json()["people"]] == ["Priya Shah"]


def test_people_falls_back_to_email_when_no_display_name(client, world):
    email = f"nn-{uuid.uuid4().hex[:6]}@example.com"
    make_user(world["org"], email=email)
    names = [p["name"] for p in client.get("/ask-friend/people", cookies=world["cookies"]).json()["people"]]
    assert email in names


def test_check_reports_slack_and_privacy(client, world):
    r = client.post("/ask-friend/check", json=_body(world), cookies=world["cookies"])
    assert r.json() == {"needs_approval": False, "slack_available": True}


def test_send_by_slack_delivers_and_records(client, world):
    r = client.post("/ask-friend/send", json=_body(world), cookies=world["cookies"])
    assert r.status_code == 200 and r.json()["sent"]["to_name"] == "Priya Shah"
    assert world["sent"][0][0] == "slack" and world["sent"][0][-1] == "S"
    assert turn_meta(world["cid"])["ask_friend_sent"]["via"] == "slack"


def test_send_to_non_member_of_space_is_403(client, world):
    ws = create_workspace(world["org"], f"s-{uuid.uuid4().hex[:6]}", world["asker"].id)
    cid = make_conversation(world["org"], world["asker"].id, ws)
    add_turn(cid, world["org"], "q?", "no", meta={"source": "x", "ask_friend": True})
    r = client.post("/ask-friend/send", json=_body(world, conversation_id=cid, question="q?", workspace_id=ws),
                    cookies=world["cookies"])
    assert r.status_code == 403 and world["sent"] == []


def test_send_on_answered_turn_is_404(client, world):
    add_turn(world["cid"], world["org"], "leave?", "4 weeks", meta={"source": "notion"})
    r = client.post("/ask-friend/send", json=_body(world, question="leave?"), cookies=world["cookies"])
    assert r.status_code == 404


def test_someone_elses_conversation_is_404(client, world):
    other = create_admin(f"o-{uuid.uuid4().hex[:8]}@example.com", world["org"])
    r = client.post("/ask-friend/send", json=_body(world), cookies={"session": create_session_token(other)})
    assert r.status_code == 404


def test_private_without_approval_is_409(client, world, monkeypatch):
    monkeypatch.setattr(service, "needs_approval", lambda *a: True)
    r = client.post("/ask-friend/send", json=_body(world), cookies=world["cookies"])
    assert r.status_code == 409 and r.json()["detail"] == "approval_required" and world["sent"] == []


def test_private_with_approval_sends(client, world, monkeypatch):
    monkeypatch.setattr(service, "needs_approval", lambda *a: True)
    r = client.post("/ask-friend/send", json=_body(world, via="email", approve_private=True), cookies=world["cookies"])
    assert r.status_code == 200 and world["sent"][0][0] == "email"


def test_slack_unavailable_is_409(client, world, monkeypatch):
    monkeypatch.setattr(service, "lookup_user_id", lambda token, email: None)
    r = client.post("/ask-friend/send", json=_body(world), cookies=world["cookies"])
    assert r.status_code == 409 and r.json()["detail"] == "slack_unavailable"


def test_second_send_for_same_turn_is_409(client, world):
    assert client.post("/ask-friend/send", json=_body(world), cookies=world["cookies"]).status_code == 200
    r = client.post("/ask-friend/send", json=_body(world, via="email"), cookies=world["cookies"])
    assert r.status_code == 409 and r.json()["detail"] == "already_sent" and len(world["sent"]) == 1


def test_failed_delivery_can_be_retried_by_email(client, world, monkeypatch):
    def fail(*a):
        raise DeliveryError("down")
    monkeypatch.setattr(service, "send_slack", fail)
    r = client.post("/ask-friend/send", json=_body(world), cookies=world["cookies"])
    assert r.status_code == 502 and r.json()["detail"] == "delivery_failed"
    assert "ask_friend_sent" not in turn_meta(world["cid"])
    r = client.post("/ask-friend/send", json=_body(world, via="email"), cookies=world["cookies"])
    assert r.status_code == 200


def test_daily_cap_refuses(client, world, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ASK_FRIEND_PER_DAY", "1")
    add_turn(world["cid"], world["org"], "second?", "no", meta={"source": "x", "ask_friend": True})
    assert client.post("/ask-friend/send", json=_body(world), cookies=world["cookies"]).status_code == 200
    r = client.post("/ask-friend/send", json=_body(world, question="second?"), cookies=world["cookies"])
    assert r.status_code == 429
