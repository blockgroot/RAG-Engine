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

- ``agent == "github"``         -> ``GitHubAgent`` (live GitHub API reads, no
  retrieval at all — see app/agent/github_agent.py). The ``workspace_id``, when
  present, is threaded through to the agent, so a workspace's Code answers come
  from **that workspace's own** installation.
- ``agent == "slack"``          -> ``SlackAgent`` (retrieval, but its pipeline is
  pinned to ``source_provider="slack"`` so it answers only from ingested Slack
  threads — see app/agent/slack_agent.py). Like GitHub it outranks
  ``workspace_id``; safety comes from the ``workspace_id`` still being threaded
  into ``answer()``, so a workspace's Slack tab retrieves only that workspace's
  own Slack chunks and never the org-wide ones.
- ``workspace_id`` set          -> ``WorkspaceAgent`` (a sub-workspace's own
  connected documents, its own pipeline — see app/agent/workspace_agent.py)
- otherwise                     -> ``PolicyAgent``, exactly as before

The explicit ``agent`` field exists because a single scope can have documents
*and* GitHub connected at once — true org-wide, and now true per workspace too —
so "route by connected source" cannot disambiguate. The client names the target
(rendered as a "Policies | Code" tab), which also keeps the user informed about
which corpus answered rather than guessing on their behalf.

**Ordering note (this changed).** ``workspace_id`` used to outrank the requested
agent, so that a workspace question could never be served org-wide GitHub
content. Workspace-scoped GitHub connections made that unnecessary *and* wrong:
``agent="github"`` now wins, and safety comes from the agent being handed the
``workspace_id`` — every GitHub read resolves its token and repo allowlist from
``(org_id, workspace_id)`` together, so a workspace with no GitHub connection
raises rather than silently reading the org-wide one. That no-fallback property
is what makes this ordering safe; it is proven in
tests/test_github_workspace_scope.py.

**v1 limitation, deliberate:** ``GitHubAgent`` has no conversation memory, so a
GitHub question is always standalone — follow-ups like "and the commit before
that?" are not resolved against history. ``/chat/conversations`` therefore
rejects ``agent="github"`` rather than handing back a conversation id that would
silently do nothing.
"""

from __future__ import annotations

import json
import logging
import os
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
#
# Default ~50ms per *word* reads as a typewriter, not a dump. Override with
# CHAT_STREAM_WORD_DELAY_MS (0 disables pacing — used by API tests).
def _stream_word_delay_seconds() -> float:
    raw = os.getenv("CHAT_STREAM_WORD_DELAY_MS")
    if raw is None or raw.strip() == "":
        return 0.05
    try:
        return max(0.0, float(raw) / 1000.0)
    except ValueError:
        return 0.05


def _word_chunks(text: str) -> Iterator[str]:
    """Yield one word at a time (trailing whitespace stays with the word)."""
    if not text:
        return
    i = 0
    n = len(text)
    while i < n:
        j = i
        while j < n and not text[j].isspace():
            j += 1
        while j < n and text[j].isspace():
            j += 1
        if j == i:
            yield text[i:]
            return
        yield text[i:j]
        i = j

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
    get_linear_agent,
    get_policy_agent,
    get_session,
    get_slack_agent,
    get_workspace_agent,
)

from .suggestions import (
    build_github_suggestions,
    build_linear_suggestions,
    build_policy_suggestions,
    build_slack_suggestions,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

AGENT_GITHUB = "github"
AGENT_POLICY = "policy"
AGENT_SLACK = "slack"
AGENT_LINEAR = "linear"

# Upper bound on a single question, in characters. Generous — a real question is
# a sentence or two — but bounded, because the question is embedded verbatim and
# the embedding model 400s on anything past its context window. Characters, not
# tokens, for the same reason chunking uses characters as its final ceiling: no
# token estimate is trustworthy on arbitrary pasted input.
MAX_QUESTION_CHARS = 4000


def _select_agent(
    workspace_id: str | None,
    requested_agent: str | None = None,
) -> RagPipelineAgent | GitHubAgent:
    """One place that decides which agent answers a request (see module docstring).

    Deterministic by construction — no LLM classifies anything here.

    Agents are loaded *lazily*: only the chosen one is constructed. FastAPI
    ``Depends(get_policy_agent)`` + ``Depends(get_workspace_agent)`` used to
    resolve *both* on every ``/chat/stream`` call, which loaded BGE-M3 and the
    cross-encoder twice into a 16GB machine and hung the Mac in swap — even for
    a GitHub-only question that needs neither model.

    ``requested_agent == "github"`` wins over ``workspace_id`` (it used to be
    the other way round). That inversion is only safe because the caller threads
    ``workspace_id`` into ``GitHubAgent.answer``, which resolves its token and
    repo allowlist from ``(org_id, workspace_id)`` together — a workspace with no
    GitHub connection therefore raises and falls back, never reads the org-wide
    installation. If you ever change that scoping, restore this ordering.
    """
    if requested_agent == AGENT_GITHUB:
        return get_github_agent()
    if requested_agent == AGENT_SLACK:
        return get_slack_agent()
    if requested_agent == AGENT_LINEAR:
        return get_linear_agent()
    if workspace_id is not None:
        return get_workspace_agent()
    return get_policy_agent()


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
    """Map provider failures to a short, non-technical message for chat.

    Never leak operator knobs (``LLM_BASE_URL``, keys, route pools) into the
    product UI — those belong in logs. Callers should still log ``exc``.
    """
    text = str(exc).lower()
    cause = getattr(exc, "cause", None)
    if cause is not None:
        text = f"{text} {cause}".lower()
    if "429" in text or "rate limit" in text or "exhausted" in text:
        return (
            "I'm getting a lot of requests right now and couldn't finish that "
            "answer. Please wait a moment and try again."
        )
    if "timeout" in text:
        return "That took too long to answer. Please try again."
    return "I couldn't reach the answer service just now. Please try again shortly."


@router.get("/suggestions")
def list_suggestions(
    agent: str = AGENT_POLICY,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """Starter questions derived from *this tenant's* connected sources.

    Never hardcoded product copy: Policies chips come from ingested document
    titles; Code chips come from the GitHub installation's stored repo list.
    Exposed to every signed-in member (not admin-only) so the Ask empty state
    works for ordinary employees — only names/titles needed for chips, not
    OAuth secrets.
    """
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    requested = (agent or AGENT_POLICY).strip().lower()
    if requested == AGENT_GITHUB:
        # Scoped exactly like the answer path: a workspace's chips come from that
        # workspace's own installation, never the org-wide one, so the suggestions
        # can't advertise repos the workspace cannot actually read.
        repos = _github_repos_for_scope(session.org_id, workspace_id)
        return {"agent": AGENT_GITHUB, "questions": build_github_suggestions(repos)}

    if requested == AGENT_SLACK:
        # Same scoping rule: chips name only channels connected to *this* scope,
        # so a workspace's Slack tab never advertises an org-wide channel it
        # cannot retrieve from.
        channels = _slack_channel_names_for_scope(session.org_id, workspace_id)
        return {"agent": AGENT_SLACK, "questions": build_slack_suggestions(channels)}

    if requested == AGENT_LINEAR:
        titles = _linear_titles_for_scope(session.org_id, workspace_id)
        return {"agent": AGENT_LINEAR, "questions": build_linear_suggestions(titles)}

    titles = _document_titles_for_scope(session.org_id, workspace_id)
    return {
        "agent": AGENT_POLICY,
        "questions": build_policy_suggestions(
            titles, workspace=workspace_id is not None
        ),
    }


def _github_repos_for_scope(org_id: str, workspace_id: str | None = None) -> list[dict]:
    """Repo list from this scope's GitHub connection's ``source_config``.

    ``IS NOT DISTINCT FROM`` pairs ``workspace_id`` with ``org_id`` rather than
    matching it alone — the same discipline every scoped query here follows. With
    ``workspace_id=None`` it selects the org-wide row exactly as before; with a
    workspace id it selects only that workspace's row, and returns ``[]`` (not the
    org's repos) when the workspace has no GitHub connection.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT source_config FROM oauth_connections "
            "WHERE org_id = %s AND provider = 'github' "
            "AND workspace_id IS NOT DISTINCT FROM %s",
            (org_id, workspace_id),
        ).fetchone()
    if not row or row[0] is None:
        return []
    config = row[0]
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            return []
    if not isinstance(config, dict):
        return []
    repos = config.get("repos") or []
    return [r for r in repos if isinstance(r, dict)]


def _slack_channel_names_for_scope(
    org_id: str, workspace_id: str | None = None
) -> list[str]:
    """Connected Slack channel names from this scope's ``source_config``.

    Same shape and same ``IS NOT DISTINCT FROM`` scoping as
    ``_github_repos_for_scope``. Returns display names (not ids); an
    unconfigured connection (connected but no channels picked yet) yields
    ``[]``, which the chip builder turns into no chips.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT source_config FROM oauth_connections "
            "WHERE org_id = %s AND provider = 'slack' "
            "AND workspace_id IS NOT DISTINCT FROM %s",
            (org_id, workspace_id),
        ).fetchone()
    if not row or row[0] is None:
        return []
    config = row[0]
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            return []
    if not isinstance(config, dict):
        return []
    names = config.get("channel_names") or {}
    channel_ids = config.get("channel_ids") or []
    if isinstance(names, dict):
        # Preserve the admin's picked order, and fall back to the id when a
        # name is missing so a chip is never rendered as an empty "#".
        return [str(names.get(cid) or cid) for cid in channel_ids] or [
            str(v) for v in names.values()
        ]
    return [str(cid) for cid in channel_ids]


def _document_titles_for_scope(org_id: str, workspace_id: str | None) -> list[str]:
    """Ingested document titles for this org (or one workspace), newest first.

    Slack rows are excluded on purpose. A Slack document is one *thread*, and
    its title is the first 80 characters of the opening message — real prose,
    not a document name. Poured into the document templates that produces
    chips like ``What does "No - it's intentionally limited, so it doesn't..."
    cover?``, which reads as broken even though every piece worked. Slack has
    its own tab and its own channel-shaped builder
    (``build_slack_suggestions``); this one is for things that have titles.

    Linear rows are excluded for the same reason "own tab" reasoning applies,
    even though an issue title reads fine on its own: a Policies chip phrased
    "What are the key rules in <issue title>?" is the wrong shape of question
    for a ticket. Linear has its own tab and builder (``build_linear_suggestions``).
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "AND source_provider IS DISTINCT FROM 'slack' "
            "AND source_provider IS DISTINCT FROM 'linear' "
            "ORDER BY created_at DESC NULLS LAST "
            "LIMIT 12",
            (org_id, workspace_id),
        ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _linear_titles_for_scope(org_id: str, workspace_id: str | None) -> list[str]:
    """Ingested Linear issue titles for this org (or one workspace), newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "AND source_provider = 'linear' "
            "ORDER BY created_at DESC NULLS LAST "
            "LIMIT 12",
            (org_id, workspace_id),
        ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


@router.post("/conversations")
def create_conversation(
    body: dict | None = None,
    session: SessionClaims = Depends(get_session),
):
    workspace_id = (body or {}).get("workspace_id")
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    # GitHubAgent has no conversation memory (see module docstring), so handing
    # back a conversation id for it would imply follow-up context that doesn't
    # exist. Refuse plainly instead of failing quietly later. Applies in a
    # workspace too, now that a workspace can have its own GitHub connection —
    # the missing capability is the agent's, not the scope's.
    if (body or {}).get("agent") == AGENT_GITHUB:
        raise HTTPException(
            status_code=400,
            detail="GitHub questions are answered standalone and do not use conversations.",
        )

    agent = _select_agent(workspace_id, (body or {}).get("agent"))
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
        _chunks, result = agent.answer_stream(
            question, org_id, conversation_id=conversation_id, workspace_id=workspace_id
        )
    except LLMProviderError as exc:
        logger.warning("Chat LLM failure: %s", exc, exc_info=True)
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return
    except ProviderError as exc:
        logger.warning("Chat provider failure: %s", exc, exc_info=True)
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return

    # Prefer word pacing over the pipeline's coarse char slices — the answer
    # is already final in ``result`` (``chunks`` would still dump in one go).
    delay = _stream_word_delay_seconds()
    for chunk in _word_chunks(result.answer):
        yield _sse_event("token", chunk)
        if delay:
            time.sleep(delay)
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
):
    check_rate_limit(f"chat:{session.org_id}:{session.user_id}")

    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="A question is required")
    if len(question) > MAX_QUESTION_CHARS:
        # The question is embedded verbatim to retrieve against, and the
        # embedding model rejects anything past its context window outright
        # (HTTP 400 INPUT_TOKEN_LIMIT_EXCEEDED) — which would surface here as an
        # opaque 500 mid-stream. Reject it up front with something actionable.
        # Same failure class as the chunk character ceiling in
        # app/ingestion/chunking.py: bound the input, don't trust a token
        # estimate of it.
        raise HTTPException(
            status_code=400,
            detail=(
                f"That question is too long ({len(question)} characters, "
                f"limit {MAX_QUESTION_CHARS}). Ask it in a shorter form."
            ),
        )

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

    agent = _select_agent(workspace_id, requested_agent)
    return StreamingResponse(
        _stream_answer(agent, question, session.org_id, conversation_id, workspace_id=workspace_id),
        media_type="text/event-stream",
    )
