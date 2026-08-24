"""Prompt-Driven Activity Scheduler, Phase 5: the HTTP surface.

Two things are actually at stake here and both get direct tests:

1. **Self-service without leaking scope.** An ordinary member (not an admin)
   must be able to do all of this, while never seeing another member's
   schedulers or another org's connections.
2. **The chat flow's tool call is untrusted input.** The model can name a
   provider the org has not connected; the endpoint must validate before
   writing, not after.

The chat tests use the real configured remote LLM (skipped without one); the
CRUD tests need no LLM at all.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import (
    OAuthTokens,
    create_admin,
    create_session_token,
    save_connection,
)
from app.auth.users import invite_member
from app.schedulers import store as sched_store

from .conftest import requires_db, requires_llm


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("EMAIL_SENDER", "console")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://portal.example.com")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


def _org_with_slack(store, org_cleanup, provider="slack"):
    """An org with a connected service, an admin, and a plain member."""
    org_id = store.create_organization(f"Sched API Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    member = invite_member(f"member-{uuid.uuid4().hex[:8]}@example.com", org_id)
    save_connection(
        org_id,
        provider,
        OAuthTokens(
            access_token="fake-token",
            refresh_token=None,
            expires_at=None,
            external_workspace_id=f"ws-{uuid.uuid4().hex[:6]}",
        ),
    )
    return org_id, member, {"session": create_session_token(member)}


@pytest.fixture
def member_org(store, org_cleanup):
    return _org_with_slack(store, org_cleanup)


# --------------------------------------------------------------------------
# Auth + scoping
# --------------------------------------------------------------------------


@requires_db
def test_every_route_requires_a_session(client):
    assert client.get("/schedulers").status_code == 401
    assert client.get("/schedulers/connections").status_code == 401
    assert client.post("/schedulers", json={}).status_code == 401


@requires_db
def test_an_ordinary_member_can_use_every_route(client, member_org):
    """Self-service is the point: no route may be admin-gated."""
    _, _, cookies = member_org

    connections = client.get("/schedulers/connections", cookies=cookies)
    assert connections.status_code == 200
    assert [c["provider"] for c in connections.json()["connections"]] == ["slack"]

    created = client.post(
        "/schedulers",
        json={"provider": "slack", "frequency": "weekly", "prompt": "What shipped?"},
        cookies=cookies,
    )
    assert created.status_code == 201, created.text
    scheduler_id = created.json()["id"]

    assert [s["id"] for s in client.get("/schedulers", cookies=cookies).json()[
        "schedulers"
    ]] == [scheduler_id]

    patched = client.patch(
        f"/schedulers/{scheduler_id}",
        json={"prompt": "What is stuck?", "frequency": "monthly"},
        cookies=cookies,
    )
    assert patched.status_code == 200
    assert patched.json()["prompt"] == "What is stuck?"
    assert patched.json()["frequency"] == "monthly"

    assert client.delete(f"/schedulers/{scheduler_id}", cookies=cookies).status_code == 204
    assert client.get("/schedulers", cookies=cookies).json()["schedulers"] == []


@requires_db
def test_a_member_never_sees_another_members_schedulers(client, store, org_cleanup):
    """Same org, different person: a scheduler is personal, not org-wide."""
    org_id, member, cookies = _org_with_slack(store, org_cleanup)
    other = invite_member(f"other-{uuid.uuid4().hex[:8]}@example.com", org_id)
    other_cookies = {"session": create_session_token(other)}

    created = client.post(
        "/schedulers",
        json={"provider": "slack", "frequency": "weekly", "prompt": "mine"},
        cookies=cookies,
    )
    scheduler_id = created.json()["id"]

    assert client.get("/schedulers", cookies=other_cookies).json()["schedulers"] == []
    assert client.patch(
        f"/schedulers/{scheduler_id}", json={"prompt": "hijack"}, cookies=other_cookies
    ).status_code == 404
    assert client.delete(
        f"/schedulers/{scheduler_id}", cookies=other_cookies
    ).status_code == 404


@requires_db
def test_a_member_never_sees_another_orgs_connections(client, store, org_cleanup):
    org_a, _, cookies_a = _org_with_slack(store, org_cleanup)
    _org_with_slack(store, org_cleanup, provider="github")

    listed = client.get("/schedulers/connections", cookies=cookies_a).json()
    assert [c["provider"] for c in listed["connections"]] == ["slack"]


@requires_db
def test_creating_against_an_unconnected_service_is_rejected(client, member_org):
    """The org has Slack, not GitHub — this must not create a doomed scheduler."""
    _, _, cookies = member_org

    response = client.post(
        "/schedulers",
        json={"provider": "github", "frequency": "weekly", "prompt": "commits"},
        cookies=cookies,
    )
    assert response.status_code == 400
    assert "not connected" in response.json()["detail"]


@requires_db
def test_unsupported_provider_and_frequency_are_rejected(client, member_org):
    _, _, cookies = member_org

    # Notion may genuinely be connected in an org, but has no activity fetcher
    # yet — offering it would create a scheduler that fails every cycle.
    assert client.post(
        "/schedulers",
        json={"provider": "notion", "frequency": "weekly", "prompt": "pages"},
        cookies=cookies,
    ).status_code == 400
    assert client.post(
        "/schedulers",
        json={"provider": "slack", "frequency": "hourly", "prompt": "x"},
        cookies=cookies,
    ).status_code == 400


@requires_db
def test_prompt_is_required_and_length_bounded(client, member_org):
    _, _, cookies = member_org

    assert client.post(
        "/schedulers",
        json={"provider": "slack", "frequency": "weekly", "prompt": "   "},
        cookies=cookies,
    ).status_code == 400
    assert client.post(
        "/schedulers",
        json={"provider": "slack", "frequency": "weekly", "prompt": "x" * 5000},
        cookies=cookies,
    ).status_code == 400


@requires_db
def test_multiple_schedulers_on_the_same_service_are_allowed(client, member_org):
    """A stated requirement: same service, different questions."""
    _, _, cookies = member_org

    for prompt in ("what shipped", "what is stuck", "what is in review"):
        assert client.post(
            "/schedulers",
            json={"provider": "slack", "frequency": "weekly", "prompt": prompt},
            cookies=cookies,
        ).status_code == 201

    assert len(client.get("/schedulers", cookies=cookies).json()["schedulers"]) == 3


# --------------------------------------------------------------------------
# Chat-driven setup
# --------------------------------------------------------------------------


@requires_db
def test_setup_chat_rejects_empty_and_overlong_conversations(client, member_org):
    _, _, cookies = member_org

    assert client.post(
        "/schedulers/setup-chat", json={"messages": []}, cookies=cookies
    ).status_code == 400
    assert client.post(
        "/schedulers/setup-chat",
        json={"messages": [{"role": "user", "content": "hi"}] * 50},
        cookies=cookies,
    ).status_code == 400


@requires_db
def test_setup_chat_never_trusts_a_hallucinated_provider(
    client, member_org, monkeypatch
):
    """The model's tool call is untrusted input — validate before writing.

    Faked so the refusal is deterministic; the org has Slack connected, and
    the model insists on Notion.
    """
    from app.llm.base import ChatResult, ToolCall

    class _Hallucinating:
        def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
            return ChatResult(
                text=None,
                tool_calls=[
                    ToolCall(
                        id="1",
                        name="create_scheduler",
                        arguments='{"provider": "notion", "frequency": "weekly", '
                        '"prompt": "page changes"}',
                    )
                ],
            )

    monkeypatch.setattr("app.llm.build_aux_llm_provider", lambda *a, **k: _Hallucinating())
    _, _, cookies = member_org

    response = client.post(
        "/schedulers/setup-chat",
        json={"messages": [{"role": "user", "content": "weekly notion changes"}]},
        cookies=cookies,
    )

    assert response.status_code == 400
    assert client.get("/schedulers", cookies=cookies).json()["schedulers"] == []


@requires_db
def test_setup_chat_asks_a_follow_up_when_the_model_makes_no_tool_call(
    client, member_org, monkeypatch
):
    from app.llm.base import ChatResult

    class _Asking:
        def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
            return ChatResult(text="How often should this run?", tool_calls=[])

    monkeypatch.setattr("app.llm.build_aux_llm_provider", lambda *a, **k: _Asking())
    _, _, cookies = member_org

    body = client.post(
        "/schedulers/setup-chat",
        json={"messages": [{"role": "user", "content": "slack report please"}]},
        cookies=cookies,
    ).json()

    assert body["done"] is False
    assert body["reply"] == "How often should this run?"
    assert client.get("/schedulers", cookies=cookies).json()["schedulers"] == []


@requires_db
def test_setup_chat_survives_a_malformed_tool_call(client, member_org, monkeypatch):
    """A model failure should re-ask, not surface a JSON parse error."""
    from app.llm.base import ChatResult, ToolCall

    class _Broken:
        def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
            return ChatResult(
                text=None,
                tool_calls=[
                    ToolCall(id="1", name="create_scheduler", arguments="{not json")
                ],
            )

    monkeypatch.setattr("app.llm.build_aux_llm_provider", lambda *a, **k: _Broken())
    _, _, cookies = member_org

    body = client.post(
        "/schedulers/setup-chat",
        json={"messages": [{"role": "user", "content": "weekly slack summary"}]},
        cookies=cookies,
    ).json()

    assert body["done"] is False
    assert body["reply"]


@requires_db
def test_setup_chat_degrades_when_the_llm_is_unreachable(
    client, member_org, monkeypatch
):
    from app.core.exceptions import ProviderError

    class _Down:
        def generate_with_tools(self, *a, **k):
            raise ProviderError("endpoint down")

    monkeypatch.setattr("app.llm.build_aux_llm_provider", lambda *a, **k: _Down())
    _, _, cookies = member_org

    response = client.post(
        "/schedulers/setup-chat",
        json={"messages": [{"role": "user", "content": "weekly slack summary"}]},
        cookies=cookies,
    )
    assert response.status_code == 503


@requires_db
@requires_llm
def test_setup_chat_creates_a_scheduler_from_one_complete_message(client, member_org):
    """REAL remote LLM: a fully-specified request should complete in one turn."""
    org_id, member, cookies = member_org

    response = client.post(
        "/schedulers/setup-chat",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Send me a weekly Slack report summarising what the team "
                        "discussed and flag anything urgent."
                    ),
                }
            ]
        },
        cookies=cookies,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["done"] is True, f"expected a tool call, got: {body}"
    assert body["scheduler"]["provider"] == "slack"
    assert body["scheduler"]["frequency"] == "weekly"
    assert body["scheduler"]["prompt"]

    stored = sched_store.list_schedulers(org_id, member.id)
    assert len(stored) == 1
    assert stored[0].id == body["scheduler"]["id"]


@requires_db
@requires_llm
def test_setup_chat_asks_before_creating_from_a_vague_request(client, member_org):
    """REAL remote LLM: incomplete request must not silently invent the slots."""
    org_id, member, cookies = member_org

    body = client.post(
        "/schedulers/setup-chat",
        json={"messages": [{"role": "user", "content": "I want a report"}]},
        cookies=cookies,
    ).json()

    assert body["done"] is False, f"should have asked, but created: {body}"
    assert body["reply"].strip()
    assert sched_store.list_schedulers(org_id, member.id) == []
