"""Chat router: streaming (SSE) question answering (Phase 13d).

The gate/recovery/grounding decision always completes BEFORE any token is
sent — ``PolicyAgent.answer_stream`` (see app/agent/policy_agent.py) runs the
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

from ..agent.policy_agent import PolicyAgent
from ..core.exceptions import LLMProviderError, ProviderError
from ..db.connection import get_connection
from ..security.rate_limit import check_rate_limit
from .deps import SessionClaims, get_policy_agent, get_session

router = APIRouter(prefix="/chat", tags=["chat"])


def _conversation_belongs_to_org(conversation_id: str, org_id: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND org_id = %s",
            (conversation_id, org_id),
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
    session: SessionClaims = Depends(get_session),
    agent: PolicyAgent = Depends(get_policy_agent),
):
    if agent.pipeline.memory is None:
        raise HTTPException(status_code=503, detail="Conversation memory is not enabled")
    conversation_id = agent.pipeline.memory.create_conversation(session.org_id)
    return {"conversation_id": conversation_id}


def _sse_event(event: str, data: dict | str) -> str:
    # Always JSON-encode, even plain token strings: SSE's "data: <line>\n\n"
    # framing breaks if the value itself contains a raw newline (e.g. a
    # markdown bullet list in the answer), which silently truncated token
    # chunks on the client. JSON-encoding guarantees a single-line payload.
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


def _stream_answer(
    agent: PolicyAgent, question: str, org_id: str, conversation_id: str | None
) -> Iterator[str]:
    try:
        chunks, result = agent.answer_stream(
            question, org_id, conversation_id=conversation_id
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
    agent: PolicyAgent = Depends(get_policy_agent),
):
    check_rate_limit(f"chat:{session.org_id}:{session.user_id}")

    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="A question is required")

    conversation_id = body.get("conversation_id")
    if conversation_id is not None and not _conversation_belongs_to_org(
        conversation_id, session.org_id
    ):
        raise HTTPException(status_code=404, detail="No such conversation for this organization")

    return StreamingResponse(
        _stream_answer(agent, question, session.org_id, conversation_id),
        media_type="text/event-stream",
    )
