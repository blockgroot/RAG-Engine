"""Chat routes for SSE answers, scoped conversations, and starter prompts."""

from __future__ import annotations

import contextvars

import json
import logging
import os
import dataclasses
import time
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
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
from ..agent.routing import (
    _ROUTING_POOL,
    EMBEDDED_PROVIDERS,
    _connected_providers,
    choose_agent,
    named_providers,
)
from ..agent.rag_pipeline_agent import RagPipelineAgent
from ..config.settings import GraphSettings
from ..core.exceptions import AuthError, LLMProviderError, ProviderError
from ..graph import plan as graph_plan
from ..livetools import LiveRequest, current_live_request, reset_live_request, use_live_request
from ..memory import personal as personal_memory
from ..memory import conversations as conversation_store
from ..llm import catalog
from ..llm import org_model
from ..llm.routed import answering_model, selected_model, use_model
from ..db.connection import get_connection
from ..feedback import record_gap
from ..guard.live import ATTACHMENT_WARNING, is_flagged, watch_question
from ..agent.orchestration import INSIGHTS_KEY
from ..insights.resolve import (
    CHART_MODE_HINT,
    asks_for_a_count,
    asks_for_a_visual,
    spec_to_dict,
)
from ..security.rate_limit import check_rate_limit
from ..security.visibility import visibility_predicate
from ..workspaces import assert_member
from .deps import (
    SessionClaims,
    get_drive_agent,
    get_github_agent,
    get_insights_agent,
    get_linear_agent,
    get_notion_agent,
    get_policy_agent,
    get_session,
    get_slack_agent,
    get_workspace_agent,
    viewer_for,
)

from .suggestions import (
    build_combined_suggestions,
    build_forms_suggestions,
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
                # Not ours to judge: offered in Chart mode with a note.
                "charts": None,
            }
        )

    return {
        "default": catalog.AUTO,
        "default_label": LLMSettings.from_env().model or "Auto",
        # The default model builds charts; Chart mode always offers it.
        "default_charts": True,
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
        "insights": get_insights_agent,
        "workspace": get_workspace_agent,
        AGENT_POLICY: get_policy_agent,
    }


def _agent_graph():
    """Build the routing graph fresh so tests see monkeypatched getters."""
    return build_agent_graph(_agent_getters())


def _conversation_belongs_to_scope(
    conversation_id: str, org_id: str, workspace_id: str | None, user_id: str
) -> bool:
    """A client-supplied conversation id must match org, workspace AND owner.

    The owner check is what makes a conversation personal, the same scoping
    ``schedulers``/``insight_pins`` already use. Without it any member holding
    another member's ``conversation_id`` could resume their chat and read the
    history -- the org+workspace pair says they may ask in this scope, never
    that this particular exchange was theirs.

    A NULL ``user_id`` is a row created before the column existed: still
    resumable by anyone in scope, because we cannot invent an owner for it and
    refusing would strand every conversation open at deploy time.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = %s AND org_id = %s "
            "AND workspace_id IS NOT DISTINCT FROM %s "
            "AND (user_id IS NULL OR user_id = %s)",
            (conversation_id, org_id, workspace_id, user_id),
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


def _may_chart_sentiment(
    org_id: str, workspace_id: str | None, session: SessionClaims
) -> bool:
    """Whether to offer survey-sentiment chips at all.

    Three conditions, all necessary: the deployment has Forms reading on, an
    admin has actually selected surveys in this scope (an allow-list, so an
    empty one means nothing is read), and this person may see the metric --
    the same `may_see_metric` the chart route uses, so a chip can never lead
    to a refusal that reveals sentiment is being collected.
    """
    try:
        from ..auth import get_connection_config
        from ..config.settings import GoogleSettings
        from ..insights import registry
        from ..insights.scopes import may_see_metric

        if not GoogleSettings.from_env().forms_enabled:
            return False
        config = get_connection_config(org_id, "google", workspace_id=workspace_id) or {}
        if not (config.get("form_ids") or []):
            return False
        return may_see_metric(
            registry.get("sentiment_by_theme"),
            role=session.role,
            workspace_id=workspace_id,
            org_id=org_id,
            user_id=session.user_id,
        )
    except Exception:  # noqa: BLE001 - chips are a convenience, never a 500
        logger.debug("Suggestions: sentiment gate check failed", exc_info=True)
        return False


def _combined_suggestions(
    org_id: str, workspace_id: str | None, session: SessionClaims
) -> dict:
    """Chips from every source connected in this scope.

    Each provider's own builder is reused unchanged, so the chips a member sees
    in the combined view are exactly the ones the pinned view would have shown
    — one place decides what a good Slack question looks like.

    Every lookup is wrapped: a single unreachable source must cost its own
    chips, never the whole empty state. Suggestions are a convenience, and a
    500 here would make Ask look broken when it works fine.
    """
    acl = viewer_for(session).acl()
    per_provider: dict[str, list[str]] = {}

    def _try(key: str, build) -> None:
        try:
            questions = build()
        except Exception:  # noqa: BLE001 - chips are a convenience
            logger.debug("Suggestions: %s lookup failed", key, exc_info=True)
            return
        if questions:
            per_provider[key] = questions

    in_space = workspace_id is not None

    _try(
        "notion",
        lambda: build_policy_suggestions(
            _titles_for_scope_by_provider(org_id, workspace_id, "notion", acl),
            workspace=in_space,
        ),
    )
    _try(
        "google",
        lambda: build_policy_suggestions(
            _titles_for_scope_by_provider(org_id, workspace_id, "google", acl),
            workspace=in_space,
        ),
    )
    _try(
        "slack",
        lambda: build_slack_suggestions(
            _slack_channel_names_for_scope(org_id, workspace_id)
        ),
    )
    _try(
        "linear",
        lambda: build_linear_suggestions(_linear_titles_for_scope(org_id, workspace_id, acl)),
    )
    _try(
        "github",
        lambda: build_github_suggestions(_github_repos_for_scope(org_id, workspace_id)),
    )

    if not per_provider.keys() & {"notion", "google"}:
        # Provider-agnostic document titles, and ONLY when no document provider
        # resolved. A tenant whose rows predate source_provider partitioning (or
        # were ingested without one) has documents that match no provider
        # filter, so without this their chips are all GitHub and every document
        # is invisible in the empty state — which is exactly what the combined
        # view exists to prevent. Conditional rather than always-on because
        # these titles are a SUPERSET of the per-provider ones: adding both
        # would show two near-identical chips for the same document.
        _try(
            "policy",
            lambda: build_policy_suggestions(
                _document_titles_for_scope(org_id, workspace_id, acl),
                workspace=in_space,
            ),
        )

    # Survey sentiment, only for someone allowed to SEE it. A chip that leads
    # to "you can't chart that here" would tell a member sentiment is being
    # collected on them, which is the one thing the gate exists to avoid.
    if _may_chart_sentiment(org_id, workspace_id, session):
        _try("forms", build_forms_suggestions)

    questions = build_combined_suggestions(per_provider, workspace=in_space)

    # No single agent produced these, and saying "policy" would be a lie the
    # client might act on.
    return {"agent": None, "sources": sorted(per_provider), "questions": questions}


@router.get("/suggestions")
def list_suggestions(
    agent: str | None = None,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """Starter questions derived from *this tenant's* connected sources.

    Never hardcoded product copy: document chips come from ingested titles,
    Code chips from the GitHub installation's stored repo list. Exposed to every
    signed-in member (not admin-only) so the Ask empty state works for ordinary
    employees — only names/titles are needed for chips, not OAuth secrets.

    **No ``agent`` means EVERY connected source**, interleaved. Ask is one box
    now, so chips from a single provider would read as "this box is for Notion"
    and would hide every other source from someone who has never asked about
    it — the empty state is where most people learn what is connected. The
    per-agent branches are kept because ``agent`` is still an accepted query
    param, and pinning one is how a caller asks "what could I ask Slack?".
    """
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    if not agent:
        return _combined_suggestions(session.org_id, workspace_id, session)

    requested = agent.strip().lower()
    # Chips are built from document titles, so they carry the same access
    # filter the answers do (see `_TITLE_ACCESS_SQL`).
    acl = viewer_for(session).acl()
    if requested == AGENT_GITHUB:
        repos = _github_repos_for_scope(session.org_id, workspace_id)
        return {"agent": AGENT_GITHUB, "questions": build_github_suggestions(repos)}

    if requested == AGENT_SLACK:
        channels = _slack_channel_names_for_scope(session.org_id, workspace_id)
        return {"agent": AGENT_SLACK, "questions": build_slack_suggestions(channels)}

    if requested == AGENT_LINEAR:
        titles = _linear_titles_for_scope(session.org_id, workspace_id, acl)
        return {"agent": AGENT_LINEAR, "questions": build_linear_suggestions(titles)}

    if requested == AGENT_NOTION:
        titles = _titles_for_scope_by_provider(session.org_id, workspace_id, "notion", acl)
        return {
            "agent": AGENT_NOTION,
            "questions": build_policy_suggestions(titles, workspace=workspace_id is not None),
        }

    if requested == AGENT_GOOGLE:
        titles = _titles_for_scope_by_provider(session.org_id, workspace_id, "google", acl)
        return {
            "agent": AGENT_GOOGLE,
            "questions": build_policy_suggestions(titles, workspace=workspace_id is not None),
        }

    titles = _document_titles_for_scope(session.org_id, workspace_id, acl)
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


# A starter chip is built from a document TITLE, so these reads carry the same
# access filter retrieval does. "What does Q3 Redundancies say?" offered to
# someone who cannot open that file discloses the one thing the filter exists
# to withhold — and the chip would then answer with a refusal, which reads as
# a broken product on top of it.
#
# `acl` is REQUIRED and an empty list means "scope-public only", never
# "everything": both call sites are member-facing, so there is no correct
# unrestricted default to offer them.
_TITLE_ACCESS_SQL = "AND " + visibility_predicate(alias=None) + " "


def _document_titles_for_scope(
    org_id: str, workspace_id: str | None, acl: list[str]
) -> list[str]:
    """Newest legacy-doc titles for this org or workspace, as ``acl`` may read."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "AND source_provider IS DISTINCT FROM 'slack' "
            "AND source_provider IS DISTINCT FROM 'linear' "
            "AND source_provider IS DISTINCT FROM 'notion' "
            "AND source_provider IS DISTINCT FROM 'google' "
            + _TITLE_ACCESS_SQL +
            "ORDER BY created_at DESC NULLS LAST "
            "LIMIT 12",
            (org_id, workspace_id, acl),
        ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _titles_for_scope_by_provider(
    org_id: str, workspace_id: str | None, provider: str, acl: list[str]
) -> list[str]:
    """Newest titles for one provider in this scope, as ``acl`` may read."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT title FROM documents "
            "WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s "
            "AND source_provider = %s "
            + _TITLE_ACCESS_SQL +
            "ORDER BY created_at DESC NULLS LAST "
            "LIMIT 12",
            (org_id, workspace_id, provider, acl),
        ).fetchall()
    return [str(r[0]) for r in rows if r and r[0]]


def _linear_titles_for_scope(
    org_id: str, workspace_id: str | None, acl: list[str]
) -> list[str]:
    """Ingested Linear issue titles for this org (or one workspace), newest first."""
    return _titles_for_scope_by_provider(org_id, workspace_id, "linear", acl)


@router.get("/conversations")
def list_conversations_route(
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """This person's chats in this scope. Every surviving one, not a page.

    Retention is the only limit (`DEFAULT_CONVERSATION_TTL_DAYS`) -- a display
    cap would leave a live chat off the end of the list, unreachable and
    undeletable, which is the state this endpoint exists to end.
    """
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    rows = conversation_store.list_conversations(
        org_id=session.org_id, user_id=session.user_id, workspace_id=workspace_id
    )
    return {
        "retention_days": conversation_store.DEFAULT_CONVERSATION_TTL_DAYS,
        "conversations": [
            {
                "id": r.id,
                "title": r.title,
                "turn_count": r.turn_count,
                "attachment_count": r.attachment_count,
                "created_at": r.created_at.isoformat(),
                "last_activity_at": r.last_activity_at.isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/conversations/{conversation_id}")
def get_conversation_route(
    conversation_id: str,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """The FULL transcript -- possible only because the fold stopped deleting.

    404 on an empty result rather than an empty list: an id that resolves to
    nothing is either not this person's or gone, and the two must not be
    distinguishable from the outside.
    """
    turns = conversation_store.get_conversation_turns(
        conversation_id=conversation_id,
        org_id=session.org_id,
        user_id=session.user_id,
        workspace_id=workspace_id,
    )
    if not turns:
        raise HTTPException(status_code=404, detail="No such conversation")
    return {
        "conversation_id": conversation_id,
        "turns": [
            {
                "turn_index": t.turn_index,
                "question": t.question,
                "answer": t.answer,
                "created_at": t.created_at.isoformat(),
                "cited": t.cited,
                "chart": t.chart,
                "meta": t.meta,
            }
            for t in turns
        ],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation_route(
    conversation_id: str,
    workspace_id: str | None = None,
    session: SessionClaims = Depends(get_session),
):
    """Delete a chat now, rather than waiting for the retention sweep.

    A sweep is not a substitute for this: someone who pastes something they
    regret should not have to wait 30 days. Attachments and turns cascade, so
    the uploaded file goes with it -- which is what pressing this means.
    """
    if not conversation_store.delete_conversation(
        conversation_id=conversation_id,
        org_id=session.org_id,
        user_id=session.user_id,
        workspace_id=workspace_id,
    ):
        raise HTTPException(status_code=404, detail="No such conversation")
    return {"deleted": True}


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
        session.org_id, workspace_id=workspace_id, user_id=session.user_id
    )
    return {"conversation_id": conversation_id}


def _sse_event(event: str, data: dict | str) -> str:
    payload = json.dumps(data)
    return f"event: {event}\ndata: {payload}\n\n"


def _done_event(conversation_id: str | None, question: str, payload: dict) -> str:
    """The ``done`` event, with who answered kept on the turn it closes.

    The pill under an answer (which agent, which tools, which files, what was
    read live, which model) used to exist only in this event, so a reopened
    chat showed answers with no provenance at all. The turn is already saved
    by now (the pipeline or ``_keep_standalone_turn``); this labels it. Never
    raises: losing the label must not lose the answer.
    """
    if conversation_id:
        try:
            conversation_store.set_last_turn_meta(
                conversation_id, question, conversation_store.meta_for_storage(payload)
            )
        except Exception:  # noqa: BLE001 - see docstring
            logger.warning("could not label the turn", exc_info=True)
    return _sse_event("done", payload)


def _answering_model() -> str | None:
    """The model that produced this request's answer, as best we can know it.

    Prefers what the endpoint reported (``response.model``), which is the only
    reliable answer once routing or a provider fallback is involved; falls back
    to what was selected. ``None`` on the default path, which the UI reads as
    "nothing to disclose" rather than printing the deployment's model to every
    member.
    """
    return answering_model() or selected_model()


def _conversation_attachments(
    org_id: str, conversation_id: str | None, session: SessionClaims | None
) -> list[tuple[str, str, bool]]:
    """``(filename, text, truncated)`` for this chat's files, oldest first.

    Empty for an anonymous or conversation-less call, and never raises: a
    failure to read attachments must degrade to answering from the corpus,
    which is the behaviour that existed before this feature.
    """
    if conversation_id is None or session is None:
        return []
    try:
        from ..attachments import load_attachment_texts

        # A flagged file (enforce mode) leads with a warning, so every path
        # that quotes it -- inline, paged, head-only -- carries it too.
        return [
            (a.filename, (ATTACHMENT_WARNING if is_flagged(a.injection_score) else "")
             + (a.content or ""), a.truncated)
            for a in load_attachment_texts(
                org_id=org_id,
                conversation_id=conversation_id,
                user_id=session.user_id,
            )
        ]
    except Exception:  # noqa: BLE001
        logger.warning("Chat: could not load attachments", exc_info=True)
        return []


def _stream_attachment_answer(
    question: str,
    attached: list[tuple[str, str, bool]],
    org_id: str,
    conversation_id: str | None,
    workspace_id: str | None,
    decision,
    session: SessionClaims | None = None,
) -> Iterator[str]:
    """Answer with the attached files AND the routed source's corpus.

    **Attachments no longer replace retrieval, and that was a real defect.**
    A member who uploads an expense receipt and asks "is this claimable?" is
    asking about the receipt AND about the policy in the corpus; answering
    from either alone answers a different question. Worse, attachments live on
    the CONVERSATION, so the old short-circuit meant every later question in
    that chat -- "how much leave do I have left?" -- was answered from the
    receipt until the file was removed.

    The routed agent's pipeline runs completely unchanged: same gate, same
    strict prompt, same audit. The files are simply also in the prompt, each
    behind its own "Attached file:" line, beside chunks that each carry their
    own provenance line -- which is what lets one answer draw on both and
    still say where each sentence came from.

    An agent with no corpus (GitHub, Insights) has nothing to blend, so those
    fall back to the files alone rather than losing the upload entirely.
    """
    names = ", ".join(name for name, _, _ in attached)
    agent = _agent_getters().get(decision.agent_key, lambda: None)()
    pipeline = getattr(agent, "pipeline", None)

    try:
        if pipeline is None:
            # GitHub/Insights: live reads and SQL, no chunks to retrieve. The
            # attachment is still the thing in front of the asker.
            result = _agent_getters()[AGENT_POLICY]().pipeline.answer_from_attachments(
                question, attached, org_id=org_id, conversation_id=conversation_id
            )
            reason = f"answered from attached file(s): {names}"
            agent_key = "attachment"
        else:
            result = pipeline.answer(
                question,
                org_id,
                conversation_id=conversation_id,
                workspace_id=workspace_id,
                attachments=attached,
                viewer=viewer_for(session),
            )
            reason = f"{decision.reason} · with attached file(s): {names}"
            agent_key = decision.agent_key
    except ProviderError as exc:
        logger.warning("Chat attachment failure: %s", exc, exc_info=True)
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return

    response = RagPipelineAgent._to_response(result)
    delay = _stream_word_delay_seconds()
    for chunk in _word_chunks(response.answer):
        yield _sse_event("token", chunk)
        if delay:
            time.sleep(delay)

    if not response.grounded and not response.access_restricted and not getattr(response, "live_withheld", False):
        record_gap(
            org_id=org_id,
            question=question,
            resolved_question=response.resolved_question,
            answer=response.answer,
            workspace_id=workspace_id,
            user_id=session.user_id if session else None,
            conversation_id=conversation_id,
            agent=agent_key,
            gate_score=response.top_score,
        )

    yield _done_event(
        conversation_id,
        question,
        {
            "answer": response.answer,
            "grounded": response.grounded,
            "source": response.source,
            "citations": [
                {"content": c.content, "reference": c.reference, "score": c.score}
                for c in response.citations
            ],
            "resolved_question": response.resolved_question,
            "latency_ms": response.latency_ms,
            "agent": agent_key,
            # Names the routed source AND the files, because with both in one
            # prompt "where did this come from?" has two answers.
            "routing_reason": reason,
            # The files that were actually in this prompt. Without it the pill
            # can only name the ROUTED agent -- so a PDF question answered from
            # the PDF displayed "Notion", a provenance claim about a source
            # that may not have contributed a word.
            "attachments": [name for name, _, _ in attached],
            "model": _answering_model(),
            "chart": None,
            "chart_period": None,
            "live_sources": list(getattr(response, "live_sources", None) or []),
            # The documents behind the answer's [n] markers; the UI draws a
            # chip only for a number listed here and strips the rest.
            "cited": list(getattr(response, "cited", None) or []),
        },
    )


def _previous_question(
    org_id: str,
    conversation_id: str | None,
    workspace_id: str | None,
    session: SessionClaims | None,
) -> str | None:
    """The last question asked in this conversation, for routing a follow-up.

    Never raises: without it a follow-up is routed on its own words, as before.
    """
    if not conversation_id or session is None:
        return None
    try:
        turns = conversation_store.get_conversation_turns(
            conversation_id=conversation_id,
            org_id=org_id,
            user_id=session.user_id,
            workspace_id=workspace_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning("could not read the previous turn for routing", exc_info=True)
        return None
    return turns[-1].question if turns else None


def _start_graph_plan(org_id, workspace_id, question, session):
    """Start this question's graph walk on the routing pool, or return None.

    Needs a real viewer: a routing or retrieval choice informed by content the
    asker cannot read is a leak, so no session means no graph at all.
    """
    if session is None:
        return None
    try:
        settings = GraphSettings.from_env()
        if not settings.retrieval_enabled:
            return None
        viewer = viewer_for(session)
        return _ROUTING_POOL.submit(
            contextvars.copy_context().run,
            graph_plan.build_plan,
            org_id,
            workspace_id,
            question,
            viewer,
            settings=settings,
        )
    except Exception:  # noqa: BLE001 - the graph may only ever add
        logger.warning("graph plan not started", exc_info=True)
        return None


#: How long the chat edge waits for a plan still being built after routing.
#: Past it, retrieval simply walks on its own, exactly as it did before plans.
_GRAPH_PLAN_WAIT_SECONDS = 1.5


def _graph_plan_result(future):
    if future is None:
        return None
    try:
        return future.result(timeout=_GRAPH_PLAN_WAIT_SECONDS)
    except Exception:  # noqa: BLE001 - slow or failed: the answer goes on without it
        logger.info("graph plan not ready; continuing without it")
        return None


def _named_connected_tools(question: str, org_id: str, workspace_id: str | None) -> set[str]:
    """Every indexed tool the question NAMES that is connected in this scope.

    Deliberately not limited to tools the graph already reached: the asker
    naming Slack is reason enough to search Slack. Limited to CONNECTED
    tools, or the pill would claim a tool that contributed nothing. The
    connection lookup runs only when a tool is actually named.
    """
    named = named_providers(question, EMBEDDED_PROVIDERS)
    if not named:
        return set()
    try:
        return named & _connected_providers(org_id, workspace_id)
    except Exception:  # noqa: BLE001 - a named tool may only ever add
        logger.warning("could not read connections for a named tool", exc_info=True)
        return set()


def _graph_connected_enabled() -> bool:
    try:
        return GraphSettings.from_env().connected_enabled
    except Exception:  # noqa: BLE001
        return False


def _invoke_with_plan(graph_input: dict, plan):
    """Run the agent graph with ``plan`` as this request's graph plan."""
    token = graph_plan.use_plan(plan)
    try:
        return _agent_graph().invoke(graph_input)["response"]
    finally:
        graph_plan.reset_plan(token)


def _keep_standalone_turn(
    agent_key: str,
    conversation_id: str | None,
    question: str,
    answer: str,
    cited: list | None = None,
    chart: dict | None = None,
) -> None:
    """Write a turn for an agent that never enters ``RagPipeline``.

    The pipeline is the only other place a turn is saved. GitHub and charts
    do not use it, and a conversation with no turn is left out of the history
    list, so the chat vanishes on reload. A failure here must not drop the
    answer the person is already reading.
    """
    if agent_key not in ("github", "insights") or not conversation_id or not (answer or "").strip():
        return
    try:
        from ..memory import build_conversation_store

        store = build_conversation_store()
        params = store.append_turn.__code__.co_varnames
        if "chart" in params:
            # The chart is saved with the turn, so a reopened chat shows it
            # again instead of the text alone.
            store.append_turn(conversation_id, question, answer, cited or None, chart)
        elif "cited" in params:
            store.append_turn(conversation_id, question, answer, cited or None)
        else:
            store.append_turn(conversation_id, question, answer)
    except Exception:  # noqa: BLE001 - the answer already exists; losing the save is the old bug
        logger.warning("could not save %s turn", agent_key, exc_info=True)


def _drop_refusal_turn(org_id, conversation_id, question, answer) -> None:
    if not conversation_id:
        return
    try:
        conversation_store.delete_last_turn_if(
            conversation_id=conversation_id, org_id=org_id, question=question, answer=answer
        )
    except Exception:  # noqa: BLE001 - a duplicate turn beats a failed answer
        logger.warning("could not drop the refused turn", exc_info=True)


def _stream_answer(
    question: str,
    org_id: str,
    conversation_id: str | None,
    workspace_id: str | None = None,
    requested_agent: str | None = None,
    model: str | None = None,
    session: SessionClaims | None = None,
    chart_mode: bool = False,
) -> Iterator[str]:
    # Who is asking, for the Second Brain's live reads (app/livetools). Whether
    # anything is read live is decided by LIVE_TOOLS_ENABLED and the gateway,
    # not here. Set inside the generator, for the reason `use_model` is.
    live_token = use_live_request(
        LiveRequest(
            org_id=org_id,
            workspace_id=workspace_id,
            user_id=session.user_id if session else None,
            conversation_id=conversation_id,
        )
    )
    # Personal memory (Second Brain layer C): this person's facts shape how
    # the question is READ, and the question itself -- never the answer -- is
    # read for new ones, alongside the answer rather than in front of it.
    facts, memory_turn = _start_personal_memory(question, org_id, conversation_id, session)
    facts_token = personal_memory.use_asker_facts(tuple((f.kind, f.text) for f in facts))
    try:
        yield from _stream_answer_body(
            question, org_id, conversation_id, workspace_id, requested_agent,
            model, session, memory_turn, chart_mode=chart_mode,
        )
    finally:
        # Starlette may close the generator from another copied context, where
        # the token is not valid; there the copy dies with the call anyway.
        for reset, token in ((reset_live_request, live_token),
                             (personal_memory.reset_asker_facts, facts_token)):
            try:
                reset(token)
            except ValueError:
                pass


#: Extraction runs beside the answer; the answer waits at most this long
#: for it before `done`, so "Remembered: ..." can be shown in the moment.
_MEMORY_GRACE_SECONDS = 1.5
_MEMORY_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="personal-memory")


@dataclass
class _MemoryTurn:
    org_id: str
    user_id: str
    #: Facts saved earlier but never shown (their extraction outlived the
    #: grace period): announced on THIS answer, so saving is never silent.
    pending: list
    future: object | None = None


def _start_personal_memory(question, org_id, conversation_id, session):
    """``(facts, turn|None)``. Never raises; off => ``([], None)``."""
    user_id = session.user_id if session else None
    try:
        if not personal_memory.is_active(org_id, user_id):
            return [], None
        facts = personal_memory.list_facts(org_id, user_id)
    except Exception:  # noqa: BLE001 - memory may only ever add
        logger.warning("personal memory unavailable", exc_info=True)
        return [], None
    turn = _MemoryTurn(org_id, user_id, pending=[f for f in facts if not f.announced])
    if conversation_id and personal_memory.worth_reading(question):
        turn.future = _MEMORY_POOL.submit(
            personal_memory.remember_from_question, question, org_id=org_id,
            user_id=user_id, conversation_id=conversation_id,
            known=tuple(f.text for f in facts),
        )
    return facts, turn


def _remembered(turn) -> list[dict]:
    """Facts to show as "Remembered: ... · Undo": any saved earlier but never
    shown, plus this question's if extraction finished within the grace. A
    slower one stays unannounced and appears on the NEXT answer."""
    if turn is None:
        return []
    facts = list(turn.pending)
    if turn.future is not None:
        try:
            facts += list(turn.future.result(timeout=_MEMORY_GRACE_SECONDS))
        except Exception:  # noqa: BLE001 - includes the timeout
            pass
    try:
        personal_memory.mark_announced(turn.org_id, turn.user_id, [f.id for f in facts])
    except Exception:  # noqa: BLE001 - shown twice beats not shown
        logger.warning("could not mark memory announced", exc_info=True)
    return [{"id": f.id, "text": f.text} for f in facts]


def _stream_answer_body(
    question: str,
    org_id: str,
    conversation_id: str | None,
    workspace_id: str | None,
    requested_agent: str | None,
    model: str | None,
    session: SessionClaims | None,
    memory_turn=None,
    chart_mode: bool = False,
) -> Iterator[str]:
    # Set inside the generator, NOT in the route that returns the
    # StreamingResponse: Starlette runs a sync generator via
    # iterate_in_threadpool, so a ContextVar set before the response is
    # returned is not reliably the context this body executes in. Setting it
    # here also resets it per stream, so a pooled thread cannot leak one
    # request's model choice into the next.
    use_model(model, org_id=org_id)
    # Logged, never refused (see guard/live.py for the measured reason).
    watch_question(question)

    # "Chart …" with Chart mode off: charts are only built in Chart mode, so
    # say where they are at once rather than spend a model call on a written
    # answer that reads as a failure. Not saved as a turn: it answers nothing.
    if not chart_mode and requested_agent is None and asks_for_a_visual(question):
        for chunk in _word_chunks(CHART_MODE_HINT):
            yield _sse_event("token", chunk)
        yield _sse_event("done", {
            "answer": CHART_MODE_HINT, "grounded": False, "source": "none",
            "citations": [], "resolved_question": None, "latency_ms": 0,
            "agent": INSIGHTS_KEY, "routing_reason": "chart-mode-off",
            "model": None, "chart": None, "chart_period": None,
            "live_sources": [], "cited": [], "chart_hint": True,
        })
        return

    # Loaded BEFORE routing but no longer instead of it. An attached file used
    # to short-circuit `choose_agent` entirely, on the reasoning that someone
    # dropping a contract in and asking "what's the notice period?" means THAT
    # contract rather than whichever source scores on the word "notice".
    #
    # That was right about the file and wrong about the corpus. "Is this bill
    # claimable?" is a question about the upload AND about the expense policy,
    # and attachments live on the CONVERSATION -- so the short-circuit also
    # meant every later question in the chat was answered from the bill. The
    # files now ride along with whatever the routed source retrieves
    # (`_stream_attachment_answer`), each context block still naming itself.
    attached = _conversation_attachments(org_id, conversation_id, session)

    # Decide WHICH agent answers before invoking the graph. `choose_agent`
    # honours an explicit `requested_agent` unchanged, so a caller that still
    # pins a source is unaffected; with none, it measures which connected
    # source's content resembles the question (app/agent/routing.py). Passing
    # the decision back in as `requested_agent` needs no graph change: a direct
    # key is honoured, and "workspace"/"policy" fall through to the same
    # default `_route` already computed.
    # The knowledge graph is walked ONCE, alongside routing rather than after
    # it (app/graph/plan.py): routing reads its exact identifiers, retrieval
    # reuses its documents instead of walking again, and the answer states its
    # facts. None when GRAPH_RETRIEVAL_ENABLED is off -- then nothing below
    # changes at all.
    plan_future = _start_graph_plan(org_id, workspace_id, question, session)
    decision = choose_agent(
        question,
        org_id,
        workspace_id=workspace_id,
        requested_agent=requested_agent,
        context=_previous_question(org_id, conversation_id, workspace_id, session),
        graph_plan=plan_future,
        viewer=viewer_for(session),
        chart_mode=chart_mode,
    )
    plan = _graph_plan_result(plan_future)
    # The classifier's live-data verdict rides the request note to the gateway
    # (reset with it when the stream ends: the outer token restores the value
    # from before this stream).
    current = current_live_request()
    if current is not None:
        use_live_request(dataclasses.replace(current, needs_live=getattr(decision, "needs_live", None)))
    connected: set[str] | None = None
    if plan is not None and _graph_connected_enabled():
        connected = graph_plan.connected_tools(
            plan, decision.agent_key, _named_connected_tools(question, org_id, workspace_id)
        )
        if connected:
            plan = plan.connected(connected, search=connected - {decision.agent_key})
    logger.info(
        "Chat routing: agent=%s reason=%s scores=%s",
        decision.agent_key,
        decision.reason,
        decision.scores,
    )

    # Routed FIRST, then blended: the router picks which corpus supports the
    # question, and the attached files join whatever it retrieves.
    if attached and not chart_mode:
        yield from _stream_attachment_answer(
            question, attached, org_id, conversation_id, workspace_id, decision, session
        )
        return

    spec = getattr(decision, "chart_spec", None)
    chart_spec = spec_to_dict(spec) if spec is not None else None

    graph_input = {
        "question": question,
        "org_id": org_id,
        "conversation_id": conversation_id,
        "workspace_id": workspace_id,
        "requested_agent": decision.agent_key,
        "stream": True,
        "chart_spec": chart_spec,
        "chart_refusal": getattr(decision, "chart_refusal", None),
        "user_id": session.user_id if session else None,
        "role": session.role if session else None,
        "viewer": viewer_for(session),
    }
    try:
        result = _invoke_with_plan(graph_input, plan)
        # ESCALATION: the routed tool refused, and the graph already proved
        # the question connects to documents the asker can open in OTHER
        # tools. One retry, as a connected answer -- never a loop, and never
        # on a withheld document (that refusal is about access, not about
        # which tool was asked). The refusal turn is removed first so the chat
        # keeps one turn per question and the retry's own follow-up rewrite
        # does not read the question it is answering as history.
        retry_tools = (
            graph_plan.escalation_tools(plan, decision.agent_key)
            if plan is not None and _graph_connected_enabled()
            and not result.grounded and not result.access_restricted
            else None
        )
        if retry_tools:
            _drop_refusal_turn(org_id, conversation_id, question, result.answer)
            connected = retry_tools
            result = _invoke_with_plan(graph_input, plan.connected(retry_tools))
    except LLMProviderError as exc:
        logger.warning("Chat LLM failure: %s", exc, exc_info=True)
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return
    except ProviderError as exc:
        logger.warning("Chat provider failure: %s", exc, exc_info=True)
        yield _sse_event("error", {"message": _user_facing_llm_error(exc)})
        return

    # A connected retry was answered by the pipeline, which already saved its
    # own turn. Saving again would put the question in the history twice.
    if not retry_tools:
        _keep_standalone_turn(
            decision.agent_key, conversation_id, question, result.answer,
            getattr(result, "cited", None),
            conversation_store.chart_for_storage(
                getattr(result, "chart", None), getattr(result, "chart_period", None)),
        )

    # A question that came back ungrounded is a documentation gap, and it is
    # recorded here without anyone having to report it -- the gaps people
    # quietly give up on are exactly the ones that never get reported.
    #
    # This is the API EDGE and not the gate inside `RagPipeline`, deliberately:
    # a gate miss is only one of the four ways an answer ends up ungrounded
    # (the strict prompt refusing on a gate-passing retrieval, the groundedness
    # audit downgrading an answer, and GitHubAgent/InsightsAgent refusing with
    # no gate at all are the others). `grounded=False` is the union of them,
    # and this is also the only place `user_id` exists.
    # A withheld document is NOT a documentation gap: the company wrote it,
    # this person simply has not been given access. Logging it would put
    # "how much leave do I have left?" on the admin's list of things nobody
    # has documented, which is the one thing that list must not contain.
    # A live read that withheld the item is not a gap either -- deliberately,
    # though a real deletion might be one: "not found or not accessible"
    # cannot tell deletion from revoked access, and a false gap sends an admin
    # to rewrite a document that exists (live-tools plan D8a).
    if not result.grounded and not result.access_restricted and not getattr(result, "live_withheld", False):
        record_gap(
            org_id=org_id,
            question=question,
            resolved_question=result.resolved_question,
            answer=result.answer,
            workspace_id=workspace_id,
            user_id=session.user_id if session else None,
            conversation_id=conversation_id,
            agent=decision.agent_key,
            gate_score=result.top_score,
        )

    delay = _stream_word_delay_seconds()
    for chunk in _word_chunks(result.answer):
        yield _sse_event("token", chunk)
        if delay:
            time.sleep(delay)
    yield _done_event(
        conversation_id,
        question,
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
            # WHICH agent answered, and why it was picked. The member no longer
            # chooses a source, so "where did this come from?" has to be
            # answerable from the response or the answer is unattributable.
            # `reason` is exposed too: a misroute is otherwise indistinguishable
            # from a source genuinely not having the answer.
            "agent": decision.agent_key,
            "routing_reason": "graph-connected" if connected else decision.reason,
            # The tools a connected answer read, so the pill can name all of
            # them: an answer drawn from Notion AND Slack labelled "Notion" is
            # unattributable in exactly the way `agent` exists to prevent.
            "connected_providers": sorted(connected) if connected else None,
            # What actually answered, resolved — never the word "auto".
            # Under a router or a provider fallback the served model differs
            # from the requested one, and "which model wrote this?" has to be
            # answerable or the picker is unfalsifiable.
            "model": _answering_model(),
            "chart": getattr(result, "chart", None),
            "chart_period": getattr(result, "chart_period", None),
            # Which connectors answered LIVE, and when. Empty when nothing was
            # refreshed -- the indexed copy answered.
            "live_sources": list(getattr(result, "live_sources", None) or []),
            "cited": list(getattr(result, "cited", None) or []),
            # "How many …" answered in words: offer Chart mode beside it.
            "chart_hint": (
                not chart_mode and decision.agent_key != INSIGHTS_KEY
                and asks_for_a_count(question)
            ),
            # Personal memory saved from this question, announced so saving is
            # never silent; the pill offers Undo.
            "remembered": _remembered(memory_turn),
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
        conversation_id, session.org_id, workspace_id, session.user_id
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
            session=session,
            # Chat's Chart toggle. Anything else is normal Ask.
            chart_mode=body.get("mode") == "chart",
        ),
        media_type="text/event-stream",
    )
