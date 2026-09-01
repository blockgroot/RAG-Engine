"""Chat routes for SSE answers, scoped conversations, and starter prompts."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

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
from ..agent.orchestration import build_agent_graph, route_agent_key
from ..agent.rag_pipeline_agent import RagPipelineAgent
from ..core.exceptions import AuthError, LLMProviderError, ProviderError
from ..llm import catalog
from ..llm import org_model
from ..llm.routed import answering_model, selected_model, use_model
from ..db.connection import get_connection
from ..security.rate_limit import check_rate_limit
from ..workspaces import assert_member
from .deps import (
    SessionClaims,
    get_drive_agent,
    get_github_agent,
    get_linear_agent,
    get_notion_agent,
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


def _is_own_model(org_id: str, model: str | None) -> bool:
    """True only for THIS org's configured model id."""
    if not model:
        return False
    own = org_model.get_org_model_summary(org_id)
    return bool(own and own["model"] == model)


@router.get("/models")
def list_models(session: SessionClaims = Depends(get_session)):
    """The models a member may pick, plus the default's identity.

    Session-gated like every other chat route — the catalog is not secret, but
    an unauthenticated endpoint that names the deployment's models is free
    reconnaissance for no benefit.

    ``models`` is empty when no selectable backend is configured
    (``OPENROUTER_API_KEY`` / ``GROQ_API_KEY``), which is how the picker hides
    itself: the frontend renders nothing rather than offering choices that
    would all silently fall back to the default.
    """
    # Asks the router which backends actually have credentials, rather than
    # checking one setting: a deployment may have a Groq key and no OpenRouter
    # key, or both. Offering a model whose backend is unconfigured would answer
    # on the default model under a label naming a model that never ran.
    from ..llm import build_llm_provider

    provider = build_llm_provider()
    backends = provider.configured_backends()
    # The default option is labelled with the deployment's actual model rather
    # than the word "Auto": "Auto" reads as a router that picks for you, when it
    # in fact means one specific model. Sent from here, not hardcoded in the
    # frontend, so it follows LLM_MODEL instead of drifting from it. The VALUE
    # stays ``catalog.AUTO`` — the sentinel is what keeps an untouched dropdown
    # byte-identical to pre-feature behaviour, so only the label moves.
    from ..config.settings import LLMSettings

    models = catalog.as_dicts(backends)

    # The org's own model, if an admin configured one. Appended rather than
    # replacing the built-ins: their key WILL break eventually (quota, rotation,
    # a retired model id), and leaving ours selectable is the member's way out
    # without waiting for an admin.
    own = org_model.get_org_model_summary(session.org_id)
    if own:
        models.append(
            {
                # Leads with the company, because `note` only renders as a hover
                # title in the composer and hover does not exist on touch — the
                # label has to carry the whole message on its own.
                "id": own["model"],
                "label": f"Your company's model — {own['model']}",
                "note": f"Configured by your admin ({own.get('preset_label') or 'custom'}).",
                "backend": "custom",
            }
        )

    return {
        "default": catalog.AUTO,
        "default_label": LLMSettings.from_env().model or "Auto",
        "models": models,
    }

AGENT_GITHUB = "github"
AGENT_POLICY = "policy"
AGENT_SLACK = "slack"
AGENT_LINEAR = "linear"
AGENT_NOTION = "notion"
AGENT_GOOGLE = "google"

MAX_QUESTION_CHARS = 4000


def _select_agent(
    workspace_id: str | None,
    requested_agent: str | None = None,
) -> RagPipelineAgent | GitHubAgent:
    """Resolve the concrete agent object for conversation creation."""
    key = route_agent_key(workspace_id, requested_agent)
    return _agent_getters()[key]()


def _agent_getters() -> dict[str, RagPipelineAgent | GitHubAgent]:
    """Build the shared routing table lazily so tests can monkeypatch getters."""
    return {
        AGENT_GITHUB: get_github_agent,
        AGENT_SLACK: get_slack_agent,
        AGENT_LINEAR: get_linear_agent,
        AGENT_NOTION: get_notion_agent,
        AGENT_GOOGLE: get_drive_agent,
        "workspace": get_workspace_agent,
        AGENT_POLICY: get_policy_agent,
    }


def _agent_graph():
    """Build the routing graph fresh so tests see monkeypatched getters."""
    return build_agent_graph(_agent_getters())


def _conversation_belongs_to_scope(
    conversation_id: str, org_id: str, workspace_id: str | None
) -> bool:
    """A client-supplied conversation id must match both org and workspace."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND org_id = %s "
            "AND workspace_id IS NOT DISTINCT FROM %s",
            (conversation_id, org_id, workspace_id),
        ).fetchone()
    return row is not None


def _user_facing_llm_error(exc: BaseException) -> str:
    """Map provider failures to a short, non-technical chat message."""
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
        repos = _github_repos_for_scope(session.org_id, workspace_id)
        return {"agent": AGENT_GITHUB, "questions": build_github_suggestions(repos)}

    if requested == AGENT_SLACK:
        channels = _slack_channel_names_for_scope(session.org_id, workspace_id)
        return {"agent": AGENT_SLACK, "questions": build_slack_suggestions(channels)}

    if requested == AGENT_LINEAR:
        titles = _linear_titles_for_scope(session.org_id, workspace_id)
        return {"agent": AGENT_LINEAR, "questions": build_linear_suggestions(titles)}

    if requested == AGENT_NOTION:
        titles = _titles_for_scope_by_provider(session.org_id, workspace_id, "notion")
        return {
            "agent": AGENT_NOTION,
            "questions": build_policy_suggestions(titles, workspace=workspace_id is not None),
        }

    if requested == AGENT_GOOGLE:
        titles = _titles_for_scope_by_provider(session.org_id, workspace_id, "google")
        return {
            "agent": AGENT_GOOGLE,
            "questions": build_policy_suggestions(titles, workspace=workspace_id is not None),
        }

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
    """Newest legacy-doc titles for this org or workspace."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "AND source_provider IS DISTINCT FROM 'slack' "
            "AND source_provider IS DISTINCT FROM 'linear' "
            "AND source_provider IS DISTINCT FROM 'notion' "
            "AND source_provider IS DISTINCT FROM 'google' "
            "ORDER BY created_at DESC NULLS LAST "
            "LIMIT 12",
            (org_id, workspace_id),
        ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _titles_for_scope_by_provider(
    org_id: str, workspace_id: str | None, provider: str
) -> list[str]:
    """Newest titles for one provider within this org or workspace."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "AND source_provider = %s "
            "ORDER BY created_at DESC NULLS LAST "
            "LIMIT 12",
            (org_id, workspace_id, provider),
        ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _linear_titles_for_scope(org_id: str, workspace_id: str | None) -> list[str]:
    """Ingested Linear issue titles for this org (or one workspace), newest first."""
    return _titles_for_scope_by_provider(org_id, workspace_id, "linear")


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
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


def _answering_model() -> str | None:
    """The model that produced this request's answer, as best we can know it.

    Prefers what the endpoint reported (``response.model``), which is the only
    reliable answer once routing or a provider fallback is involved; falls back
    to what was selected. ``None`` on the default path, which the UI reads as
    "nothing to disclose" rather than printing the deployment's model to every
    member.
    """
    return answering_model() or selected_model()


def _stream_answer(
    question: str,
    org_id: str,
    conversation_id: str | None,
    workspace_id: str | None = None,
    requested_agent: str | None = None,
    model: str | None = None,
) -> Iterator[str]:
    # Set inside the generator, NOT in the route that returns the
    # StreamingResponse: Starlette runs a sync generator via
    # iterate_in_threadpool, so a ContextVar set before the response is
    # returned is not reliably the context this body executes in. Setting it
    # here also resets it per stream, so a pooled thread cannot leak one
    # request's model choice into the next.
    use_model(model, org_id=org_id)
    try:
        state = _agent_graph().invoke(
            {
                "question": question,
                "org_id": org_id,
                "conversation_id": conversation_id,
                "workspace_id": workspace_id,
                "requested_agent": requested_agent,
                "stream": True,
            }
        )
        result = state["response"]
    except LLMProviderError as exc:
        logger.warning("Chat LLM failure: %s", exc, exc_info=True)
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return
    except ProviderError as exc:
        logger.warning("Chat provider failure: %s", exc, exc_info=True)
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return

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
            # What actually answered, resolved — never the word "auto".
            # Under a router or a provider fallback the served model differs
            # from the requested one, and "which model wrote this?" has to be
            # answerable or the picker is unfalsifiable.
            "model": _answering_model(),
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
        raise HTTPException(
            status_code=400,
            detail=(
                f"That question is too long ({len(question)} characters, "
                f"limit {MAX_QUESTION_CHARS}). Ask it in a shorter form."
            ),
        )

    requested_agent = body.get("agent")
    # Validated HERE, before the StreamingResponse exists: once a stream's
    # headers are sent, a raise inside the generator can no longer produce a
    # status code, so the caller would see a truncated 200 instead of a 400.
    # A client-supplied model string is untrusted input like any other field —
    # it must never reach an outbound call or a cache key unchecked.
    model = body.get("model")
    if not catalog.is_selectable(model) and not _is_own_model(session.org_id, model):
        # Fails CLOSED. Accepting an unknown id would fall through to the
        # OpenRouter branch in RoutedLLMProvider and spend the deployment's own
        # key — an account-wide 50/day quota shared by every tenant.
        raise HTTPException(status_code=400, detail="Unknown model")

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

    return StreamingResponse(
        _stream_answer(
            question,
            session.org_id,
            conversation_id,
            workspace_id=workspace_id,
            requested_agent=requested_agent,
            model=model,
        ),
        media_type="text/event-stream",
    )
