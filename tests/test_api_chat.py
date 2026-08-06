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
from app.agent.workspace_agent import WorkspaceAgent
from app.config.settings import RagSettings, RecoverySettings, ReuseSettings
from app.memory import build_conversation_store
from app.rag.pipeline import RagPipeline
from app.rag.prompts import WORKSPACE_PROMPT_PROFILE

from .conftest import requires_db
from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

FALLBACK = "I don't have information on that in the available policy documents."
WORKSPACE_FALLBACK = "I don't have anything about that in this workspace's connected content."


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


def _fake_workspace_agent(org_id: str, *, memory=None) -> WorkspaceAgent:
    """Mirrors ``_fake_agent`` but with the workspace prompt profile + fallback,
    and store content that answers a workspace-flavored question — so a test
    can tell (via ``source``/answer text) which agent actually ran."""
    llm = RecordingLLM(answer="The launch was delayed to October 15th.")
    store = TopicAwareVectorStore(
        org_id, [("doc-1", "The Q3 launch was delayed to October 15th.")]
    )
    pipeline = RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(
            top_k=3, similarity_threshold=0.35, fallback_response=WORKSPACE_FALLBACK
        ),
        memory=memory,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
        prompt_profile=WORKSPACE_PROMPT_PROFILE,
    )
    return WorkspaceAgent(pipeline)


@pytest.fixture
def client_and_session(store, org_cleanup):
    from app.api.deps import get_policy_agent, get_workspace_agent
    from app.api.main import create_app
    from app.auth import create_admin, create_session_token

    org_id = store.create_organization(f"Chat API Test Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    user = create_admin(f"chatapi-{uuid.uuid4().hex[:8]}@example.com", org_id)
    token = create_session_token(user)

    app = create_app()
    memory = build_conversation_store()
    app.dependency_overrides[get_policy_agent] = lambda: _fake_agent(org_id, memory=memory)
    # Every chat route now also depends on get_workspace_agent (Workspace Agent
    # split routing) even for org-wide requests -- override it too so no test
    # accidentally builds the real, heavy singleton (embedding/reranker models).
    app.dependency_overrides[get_workspace_agent] = lambda: _fake_workspace_agent(
        org_id, memory=memory
    )

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

    reassembled = "".join(json.loads(data) for _, data in token_events)
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
def test_chat_stream_emits_error_event_when_llm_is_rate_limited(client_and_session):
    """FreeLLMAPI 429 must not crash the ASGI stream — surface a chat error."""
    from app.api.deps import get_policy_agent
    from app.core.exceptions import LLMProviderError

    client, cookies, _org_id, _ = client_and_session

    class _BoomAgent:
        def answer_stream(self, question, org_id, conversation_id=None, workspace_id=None):
            raise LLMProviderError(
                "LLM API error: Error code: 429 - All models exhausted"
            )

    client.app.dependency_overrides[get_policy_agent] = lambda: _BoomAgent()

    response = client.post(
        "/chat/stream",
        json={"question": "How many leave days?"},
        cookies=cookies,
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events, "expected an SSE error event"
    assert events[0][0] == "error"
    payload = json.loads(events[0][1])
    assert "rate-limited" in payload["message"].lower()


def test_user_facing_llm_error_detects_exhausted_routes():
    from app.api.chat import _user_facing_llm_error
    from app.core.exceptions import LLMProviderError

    msg = _user_facing_llm_error(
        LLMProviderError("Error code: 429 - All models exhausted: 60 routes checked")
    )
    assert "rate-limited" in msg.lower()


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


# --- Workspace-within-a-Workspace: chat scoped to a sub-workspace (Task 10) ---


@requires_db
def test_chat_stream_rejects_workspace_id_for_non_member(client_and_session, store, org_cleanup):
    from app.auth.users import create_admin
    from app.workspaces import create_workspace

    client, _cookies, org_id, _memory = client_and_session
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    stranger = create_admin(f"stranger-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)

    from app.auth import create_session_token

    stranger_cookies = {"session": create_session_token(stranger)}
    response = client.post(
        "/chat/stream",
        json={"question": "What was decided?", "workspace_id": workspace_id},
        cookies=stranger_cookies,
    )
    assert response.status_code == 403


@requires_db
def test_create_conversation_scoped_to_workspace(client_and_session, store, org_cleanup):
    from app.auth.users import create_admin
    from app.workspaces import create_workspace

    client, cookies, org_id, memory = client_and_session
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)

    from app.auth import create_session_token

    owner_cookies = {"session": create_session_token(owner)}
    response = client.post(
        "/chat/conversations", json={"workspace_id": workspace_id}, cookies=owner_cookies
    )
    assert response.status_code == 200
    conversation_id = response.json()["conversation_id"]

    from app.db.connection import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT workspace_id::text FROM conversations WHERE id = %s", (conversation_id,)
        ).fetchone()
    assert row[0] == workspace_id


@requires_db
def test_workspace_conversation_id_rejected_without_matching_workspace_id(
    client_and_session, store, org_cleanup
):
    """A conversation created under workspace A must not be usable via the
    org-wide chat (no workspace_id) or a sibling workspace -- proves the
    conversation_id scope check is keyed on workspace_id, not just org_id."""
    from app.auth.users import create_admin
    from app.workspaces import create_workspace

    client, cookies, org_id, memory = client_and_session
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_a = create_workspace(org_id, "Workspace A", owner.id)
    workspace_b = create_workspace(org_id, "Workspace B", owner.id)

    from app.auth import create_session_token

    owner_cookies = {"session": create_session_token(owner)}
    create_resp = client.post(
        "/chat/conversations", json={"workspace_id": workspace_a}, cookies=owner_cookies
    )
    conversation_id = create_resp.json()["conversation_id"]

    # Org-wide chat (no workspace_id) must not accept workspace A's conversation.
    org_wide_response = client.post(
        "/chat/stream",
        json={"question": "hi", "conversation_id": conversation_id},
        cookies=owner_cookies,
    )
    assert org_wide_response.status_code == 404

    # Sibling workspace B must not accept workspace A's conversation either.
    sibling_response = client.post(
        "/chat/stream",
        json={
            "question": "hi",
            "conversation_id": conversation_id,
            "workspace_id": workspace_b,
        },
        cookies=owner_cookies,
    )
    assert sibling_response.status_code == 404

    # The matching workspace accepts it.
    matching_response = client.post(
        "/chat/stream",
        json={
            "question": "How many annual leave days?",
            "conversation_id": conversation_id,
            "workspace_id": workspace_a,
        },
        cookies=owner_cookies,
    )
    assert matching_response.status_code == 200


# --- Workspace Agent split: routing picks WorkspaceAgent, not PolicyAgent ---


@requires_db
def test_org_wide_chat_uses_policy_agent(client_and_session):
    """No workspace_id -> PolicyAgent (unchanged) answers, source == 'policy'."""
    client, cookies, org_id, _memory = client_and_session
    response = client.post(
        "/chat/stream",
        json={"question": "How many annual leave days do I get?"},
        cookies=cookies,
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    done = json.loads([e for e in events if e[0] == "done"][0][1])
    assert done["source"] == "policy"
    assert done["answer"] == "You get 25 days of annual leave. [1]"


@requires_db
def test_workspace_chat_uses_workspace_agent(client_and_session, store, org_cleanup):
    """A workspace_id request is answered by WorkspaceAgent, source == 'workspace'."""
    from app.auth import create_session_token
    from app.auth.users import create_admin
    from app.workspaces import create_workspace

    client, _cookies, org_id, _memory = client_and_session
    owner = create_admin(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)
    workspace_id = create_workspace(org_id, "Meeting Notes", owner.id)
    owner_cookies = {"session": create_session_token(owner)}

    response = client.post(
        "/chat/stream",
        json={"question": "What was decided about the launch?", "workspace_id": workspace_id},
        cookies=owner_cookies,
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    done = json.loads([e for e in events if e[0] == "done"][0][1])
    assert done["source"] == "workspace"
    assert "October 15th" in done["answer"]


@requires_db
def test_suggestions_from_connected_sources(client_and_session, store):
    """Chips come from this org's docs / GitHub repos — not hardcoded copy."""
    client, cookies, org_id, _ = client_and_session
    from app.db.connection import get_connection
    import json as _json

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO documents (org_id, title, source_uri) VALUES (%s, %s, %s)",
            (org_id, "Parental Leave Handbook", "notion://example"),
        )
        conn.execute(
            """
            INSERT INTO oauth_connections (
              org_id, provider, external_workspace_id, external_workspace_name,
              access_token_encrypted, source_config
            ) VALUES (
              %s, 'github', 'inst-1', '18sana', %s, %s::jsonb
            )
            """,
            (
                org_id,
                "encrypted-test",
                _json.dumps(
                    {
                        "repository_selection": "selected",
                        "repos": [
                            {
                                "full_name": "18sana/LiveDemoRepo",
                                "description": "Demo app",
                                "topics": [],
                            }
                        ],
                    }
                ),
            ),
        )
        conn.commit()

    policy = client.get("/chat/suggestions?agent=policy", cookies=cookies)
    assert policy.status_code == 200
    pq = policy.json()["questions"]
    assert pq and any("Parental Leave Handbook" in q for q in pq)
    assert all("maternity" not in q.lower() for q in pq)

    code = client.get("/chat/suggestions?agent=github", cookies=cookies)
    assert code.status_code == 200
    cq = code.json()["questions"]
    assert cq and any("LiveDemoRepo" in q for q in cq)
    assert all("Fact-Verification" not in q for q in cq)
