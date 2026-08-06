"""Chat router: streaming (SSE) question answering (Phase 13d).

The gate/recovery/grounding decision always completes BEFORE any token is
sent — ``answer_stream`` (see app/agent/rag_pipeline_agent.py) runs the
full, unchanged answer path first and only then hands back an iterator over
the already-decided answer text. This endpoint's job is purely transport: turn
that iterator into Server-Sent Events and attach the terminal metadata
(citations, source, latency) once the text is fully sent.

A client-supplied ``conversation_id`` is a cross-tenant risk the underlying
``ConversationStore`` interface does not itself guard (by design, for its
trusted internal callers — see app/memory/base.py): the store's own
``get_context``/``append_turn`` take only a ``conversation_id``, no org check.
Exposed over HTTP to arbitrary clients, an org could otherwise probe another
org's conversation by guessing/reusing its id. So this router is the one place
that must verify a supplied ``conversation_id`` actually belongs to the
caller's ``org_id`` before ever handing it to the agent.

Agent routing: ``_select_agent`` is the one place the decision is made, and it
is entirely deterministic — no LLM ever classifies a question to pick an agent,
because a non-deterministic step in front of the tenant-scoped path is exactly
what the confidence gate's design philosophy avoids.

- ``workspace_id`` set          -> ``WorkspaceAgent`` (a sub-workspace's own
  connected content, its own pipeline — see app/agent/workspace_agent.py)
- ``agent == "github"``         -> ``GitHubAgent`` (live GitHub API reads, no
  retrieval at all — see app/agent/github_agent.py)
- otherwise                     -> ``PolicyAgent``, exactly as before

The explicit ``agent`` field exists because GitHub connects at the **org** level,
so an org commonly has Notion/Drive policies *and* GitHub connected at once. At
org scope "route by connected source" cannot disambiguate, so the client names
the target (rendered as a "Policies | Code" tab). Naming it also keeps the user
informed about which corpus answered, rather than guessing on their behalf.

**v1 limitation, deliberate:** ``GitHubAgent`` has no conversation memory, so a
GitHub question is always standalone — follow-ups like "and the commit before
that?" are not resolved against history. ``/chat/conversations`` therefore
rejects ``agent="github"`` rather than handing back a conversation id that would
silently do nothing.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

# Chunks are sliced from an already-fully-generated answer (see module
# docstring) with no natural gap between them, so without an artificial pace
# they all arrive over the wire back-to-back — indistinguishable from a bulk
# response despite being "streamed". This is purely a transport-layer delay
# (StreamingResponse runs this sync generator in a worker thread, so
# time.sleep here doesn't block the event loop or other requests) — it does
# not touch RagPipeline.answer_stream, which stays instant/deterministic for
# tests and the CLI.
_CHUNK_DELAY_SECONDS = 0.02

from ..agent.github_agent import GitHubAgent
from ..agent.policy_agent import PolicyAgent
from ..agent.rag_pipeline_agent import RagPipelineAgent
from ..agent.workspace_agent import WorkspaceAgent
from ..core.exceptions import AuthError, LLMProviderError, ProviderError
from ..db.connection import get_connection
from ..security.rate_limit import check_rate_limit
from ..workspaces import assert_member
from .deps import (
    SessionClaims,
    get_github_agent,
    get_policy_agent,
    get_session,
    get_workspace_agent,
)

router = APIRouter(prefix="/chat", tags=["chat"])

AGENT_GITHUB = "github"


def _select_agent(
    workspace_id: str | None,
    policy_agent: PolicyAgent,
    workspace_agent: WorkspaceAgent,
    github_agent: GitHubAgent | None = None,
    requested_agent: str | None = None,
) -> RagPipelineAgent | GitHubAgent:
    """One place that decides which agent answers a request (see module docstring).

    Deterministic by construction. ``workspace_id`` wins over ``requested_agent``
    because a sub-workspace is a narrower data boundary than a source choice: a
    workspace member asking inside their workspace must never be silently served
    org-wide GitHub content instead.
    """
    if workspace_id is not None:
        return workspace_agent
    if requested_agent == AGENT_GITHUB and github_agent is not None:
        return github_agent
    return policy_agent


def _conversation_belongs_to_scope(
    conversation_id: str, org_id: str, workspace_id: str | None
) -> bool:
    """A client-supplied conversation_id must match BOTH org_id and workspace_id.

    Workspace-within-a-Workspace: without this, a workspace conversation_id
    could be replayed against the org-wide chat (or a sibling workspace's
    chat) for the same org_id. ``IS NOT DISTINCT FROM`` so workspace_id=None
    correctly matches an org-wide conversation's NULL column.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND org_id = %s "
            "AND workspace_id IS NOT DISTINCT FROM %s",
            (conversation_id, org_id, workspace_id),
        ).fetchone()
    return row is not None


def _user_facing_llm_error(exc: BaseException) -> str:
    """Map provider failures to a short message the chat UI can show."""
    text = str(exc).lower()
    cause = getattr(exc, "cause", None)
    if cause is not None:
        text = f"{text} {cause}".lower()
    if "429" in text or "rate limit" in text or "exhausted" in text:
        return (
            "The AI service is temporarily rate-limited (all free routes busy). "
            "Wait a minute and try again, or add more keys / switch LLM_BASE_URL."
        )
    if "timeout" in text:
        return "The AI service timed out. Please try again."
    return "The AI service is unavailable right now. Please try again shortly."


@router.post("/conversations")
def create_conversation(
    body: dict | None = None,
    session: SessionClaims = Depends(get_session),
    policy_agent: PolicyAgent = Depends(get_policy_agent),
    workspace_agent: WorkspaceAgent = Depends(get_workspace_agent),
):
    workspace_id = (body or {}).get("workspace_id")
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    # GitHubAgent has no conversation memory (see module docstring), so handing
    # back a conversation id for it would imply follow-up context that doesn't
    # exist. Refuse plainly instead of failing quietly later.
    if (body or {}).get("agent") == AGENT_GITHUB and workspace_id is None:
        raise HTTPException(
            status_code=400,
            detail="GitHub questions are answered standalone and do not use conversations.",
        )

    agent = _select_agent(workspace_id, policy_agent, workspace_agent)
    if agent.pipeline.memory is None:
        raise HTTPException(status_code=503, detail="Conversation memory is not enabled")

    conversation_id = agent.pipeline.memory.create_conversation(
        session.org_id, workspace_id=workspace_id
    )
    return {"conversation_id": conversation_id}


def _sse_event(event: str, data: dict | str) -> str:
    # Always JSON-encode, even plain token strings: SSE's "data: <line>\n\n"
    # framing breaks if the value itself contains a raw newline (e.g. a
    # markdown bullet list in the answer), which silently truncated token
    # chunks on the client. JSON-encoding guarantees a single-line payload.
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


def _stream_answer(
    agent: RagPipelineAgent | GitHubAgent,
    question: str,
    org_id: str,
    conversation_id: str | None,
    workspace_id: str | None = None,
) -> Iterator[str]:
    try:
        chunks, result = agent.answer_stream(
            question, org_id, conversation_id=conversation_id, workspace_id=workspace_id
        )
    except LLMProviderError as exc:
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return
    except ProviderError as exc:
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return

    for chunk in chunks:
        yield _sse_event("token", chunk)
        time.sleep(_CHUNK_DELAY_SECONDS)
    yield _sse_event(
        "done",
        {
            "answer": result.answer,
            "grounded": result.grounded,
            "source": result.source,
            "citations": [
                {"content": c.content, "reference": c.reference, "score": c.score}
                for c in result.citations
            ],
            "resolved_question": result.resolved_question,
            "latency_ms": result.latency_ms,
        },
    )


@router.post("/stream")
def chat_stream(
    body: dict,
    session: SessionClaims = Depends(get_session),
    policy_agent: PolicyAgent = Depends(get_policy_agent),
    workspace_agent: WorkspaceAgent = Depends(get_workspace_agent),
    github_agent: GitHubAgent = Depends(get_github_agent),
):
    check_rate_limit(f"chat:{session.org_id}:{session.user_id}")

    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="A question is required")

    requested_agent = body.get("agent")
    workspace_id = body.get("workspace_id")
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    conversation_id = body.get("conversation_id")
    if conversation_id is not None and not _conversation_belongs_to_scope(
        conversation_id, session.org_id, workspace_id
    ):
        raise HTTPException(status_code=404, detail="No such conversation for this organization")

    agent = _select_agent(
        workspace_id, policy_agent, workspace_agent, github_agent, requested_agent
    )
    return StreamingResponse(
        _stream_answer(agent, question, session.org_id, conversation_id, workspace_id=workspace_id),
        media_type="text/event-stream",
    )
