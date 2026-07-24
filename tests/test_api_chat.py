"""Phase 13d: chat router (SSE streaming).

The agent itself is faked (RecordingLLM + KeywordEmbedder + TopicAwareVectorStore,
same fakes as test_streaming.py/test_recovery.py) via a FastAPI dependency
override, so these tests never load a real embedding/reranker model or call a
real LLM. Conversation memory is the REAL Postgres-backed store (requires_db)
because the conversation-ownership check queries the real `conversations`
table directly — that's the property under test.
"""

from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.agent.policy_agent import PolicyAgent
from app.config.settings import RagSettings, RecoverySettings, ReuseSettings
from app.memory import build_conversation_store
from app.rag.pipeline import RagPipeline

from .conftest import requires_db
from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

FALLBACK = "I don't have information on that in the available policy documents."


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")


def _fake_agent(org_id: str, *, memory=None) -> PolicyAgent:
    llm = RecordingLLM(answer="You get 25 days of annual leave. [1]")
    store = TopicAwareVectorStore(
        org_id, [("doc-1", "Employees get 25 days of paid annual leave per year.")]
    )
    pipeline = RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(top_k=3, similarity_threshold=0.35, fallback_response=FALLBACK),
        memory=memory,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
    )
    return PolicyAgent(pipeline)


@pytest.fixture
def client_and_session(store, org_cleanup):
    from app.api.deps import get_policy_agent
    from app.api.main import create_app
    from app.auth import create_admin, create_session_token

    org_id = store.create_organization(f"Chat API Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    user = create_admin(f"chatapi-{uuid.uuid4().hex[:8]}@example.com", org_id)
    token = create_session_token(user)

    app = create_app()
    memory = build_conversation_store()
    app.dependency_overrides[get_policy_agent] = lambda: _fake_agent(org_id, memory=memory)

    client = TestClient(app)
    return client, {"session": token}, org_id, memory


def _parse_sse(raw_text: str) -> list[tuple[str, str]]:
    events = []
    for block in raw_text.strip().split("\n\n"):
        if not block.strip():
            continue
        lines = block.strip().split("\n")
        event = lines[0].removeprefix("event: ")
        data = lines[1].removeprefix("data: ")
        events.append((event, data))
    return events


@requires_db
def test_chat_stream_requires_a_session():
    from app.api.main import create_app

    client = TestClient(create_app())
    response = client.post("/chat/stream", json={"question": "How many leave days?"})
    assert response.status_code == 401


@requires_db
def test_chat_stream_requires_a_question(client_and_session):
    client, cookies, org_id, _ = client_and_session
    response = client.post("/chat/stream", json={"question": "  "}, cookies=cookies)
    assert response.status_code == 400


@requires_db
def test_chat_stream_returns_tokens_then_done_event(client_and_session):
    client, cookies, org_id, _ = client_and_session
    response = client.post(
        "/chat/stream",
        json={"question": "How many annual leave days do I get?"},
        cookies=cookies,
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    token_events = [e for e in events if e[0] == "token"]
    done_events = [e for e in events if e[0] == "done"]

    assert len(token_events) > 0
    assert len(done_events) == 1

    reassembled = "".join(data for _, data in token_events)
    done_payload = json.loads(done_events[0][1])
    assert reassembled == done_payload["answer"] == "You get 25 days of annual leave. [1]"
    assert done_payload["grounded"] is True
    assert done_payload["source"] == "policy"


@requires_db
def test_chat_stream_rejects_conversation_id_from_another_org(
    client_and_session, store, org_cleanup
):
    client, cookies, org_id, memory = client_and_session

    other_org = store.create_organization(f"Chat API Other Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other_org)
    other_conversation_id = memory.create_conversation(other_org)

    response = client.post(
        "/chat/stream",
        json={"question": "How many annual leave days?", "conversation_id": other_conversation_id},
        cookies=cookies,
    )
    assert response.status_code == 404


@requires_db
def test_create_conversation_scoped_to_session_org(client_and_session):
    client, cookies, org_id, memory = client_and_session
    response = client.post("/chat/conversations", cookies=cookies)
    assert response.status_code == 200
    conversation_id = response.json()["conversation_id"]

    # The created conversation must actually be scoped to this org in the DB.
    from app.db.connection import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id::text FROM conversations WHERE id = %s", (conversation_id,)
        ).fetchone()
    assert row[0] == org_id

    # And using it on the next chat call must be accepted (not 404).
    chat_response = client.post(
        "/chat/stream",
        json={"question": "How many annual leave days?", "conversation_id": conversation_id},
        cookies=cookies,
    )
    assert chat_response.status_code == 200
