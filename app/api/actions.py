"""Act mode routes (docs/plans/2026-10-13-write-actions.md).

A separate surface from Ask and Chart: its own page, its own routes, its own
package. ``org_id`` and the person come from the session here, as everywhere;
an action id from the URL is only ever looked up together with both.

Which tools exist and each person's sign-in to them are MCP's
(``api/mcp_actions.py``); these routes draft, edit, approve and switch.
"""

from __future__ import annotations

import itertools
import logging
import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..actions import policy
from ..actions.factory import get_action
from ..actions import service, store
from ..actions.exposure import Exposure
from ..actions.store import ActionRow
from ..auth.users import get_user
from ..config.settings import ActionsSettings
from ..core.exceptions import (
    ActionConflict,
    ActionError,
    ActionExpired,
    AuthError,
    ConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    NotFoundError,
    OAuthReauthRequiredError,
)
from ..security.rate_limit import check_rate_limit
from ..workspaces import assert_member
from .deps import (
    SessionClaims,
    get_session,
    get_workspace_role,
    require_admin,
    require_workspace_owner,
    viewer_for,
)

logger = logging.getLogger("actions")

router = APIRouter(tags=["act"])


@lru_cache(maxsize=1)
def _llm():
    from ..llm import build_llm_provider

    return build_llm_provider()


def _embedder():
    from ..embeddings import build_embedding_provider

    return build_embedding_provider()  # process-wide cached


@lru_cache(maxsize=1)
def _vstore():
    from ..vectorstore import build_vector_store

    return build_vector_store()


def ask_search(user_id: str):
    """How Act finds content: Ask's own reading, never a search of its own.

    Routing is Ask's (``choose_agent``: a tool the words name, else the best
    match by meaning, with the knowledge graph's plan when it is on), run on
    the words that name the content: "create a Linear issue from the Slack
    message about X" names Linear as where it GOES and Slack as where it IS.

    Several tools named ("the Slack discussion and the Notion spec") are each
    read, as the chat's connected answer reads exactly the tools a question
    names; the passages are interleaved so every tool gets its share. With the
    graph on, its plan is used exactly as the chat uses it, so linked
    documents from other tools come in too, walked as this viewer.

    An indexed tool is searched by that agent's pipeline as Ask does
    (``RagPipeline.retrieve_passages``); GitHub is read live by Ask's GitHub
    agent (``read_evidence``), limited to the repositories the asker can open.
    A Slack channel's draft stays in that channel, read by the room, like a
    channel reply. Charts hold no passages, so they fall back to the
    company-wide (or space) search.
    """
    from ..actions.base import Source
    from ..actions.service import indexed_context
    from ..agent.routing import choose_agent, named_providers
    from ..config.settings import GraphSettings
    from ..graph import plan as graph_plan
    from ..rag.deep import reset_deep_read, use_deep_read
    from . import chat, deps

    per_source = {"slack": deps.get_slack_agent, "linear": deps.get_linear_agent,
                  "notion": deps.get_notion_agent, "google": deps.get_drive_agent}

    def general(workspace_id):
        return deps.get_workspace_agent if workspace_id else deps.get_policy_agent

    def read_github(text, org_id, workspace_id, viewer):
        read = deps.get_github_agent().read_evidence(
            text, org_id, workspace_id=workspace_id, viewer=viewer)
        if read is None:
            return None
        repo, evidence, public, url = read
        # A private repository's readers are not known here, so nobody counts
        # as able to see it: any audience gets the tick.
        return Source(f"github:{repo}", f"GitHub {repo}", public, (), url), (
            f"[GitHub {repo}]\n{evidence}")

    def search(question, about, *, org_id, workspace_id=None, tags=None, viewer=None, days=None):
        # Act drafts from what it reads, so it reads as Deep analysis does:
        # the best documents with their neighbouring chunks, not six pieces.
        deep = use_deep_read(True)
        try:
            return _search(question, about, org_id=org_id, workspace_id=workspace_id, tags=tags,
                           viewer=viewer, days=days)
        finally:
            reset_deep_read(deep)

    def _search(question, about, *, org_id, workspace_id, tags, viewer, days):
        text = about or question
        if tags:  # a Slack channel: that channel only, as the room
            hits = per_source["slack"]().pipeline.retrieve_passages(
                question, org_id, about=about, workspace_id=workspace_id, tags=tags, viewer=viewer)
            return indexed_context(hits, org_id, viewer, workspace_id, days=days)

        try:
            plan = graph_plan.build_plan(org_id, workspace_id, text, viewer)
        except Exception:  # noqa: BLE001 - the graph may only ever add
            plan = None
        try:
            key = choose_agent(text, org_id, workspace_id=workspace_id, graph_plan=plan,
                               viewer=viewer, user_id=user_id).agent_key
        except Exception:  # noqa: BLE001 - routing is a narrowing, never a requirement
            logger.warning("Act routing failed; searching everything", exc_info=True)
            key = None
        named = set(chat._named_connected_tools(text, org_id, workspace_id))
        named |= named_providers(text, ("github",))
        tools = sorted(named) if len(named) > 1 else [key]
        if plan is not None and GraphSettings.from_env().connected_enabled:
            connected = graph_plan.connected_tools(plan, key, named & set(per_source))
            if connected:
                plan = plan.connected(connected, search=connected - {key})

        sources, texts, per_tool = [], [], []
        token = graph_plan.use_plan(plan)
        try:
            for tool in tools:
                # One tool failing (a renamed repo, an expired token) must not
                # blank the others: the draft would then say nothing was found.
                try:
                    if tool in ("github", "github_live"):
                        read = read_github(text, org_id, workspace_id, viewer)
                        if read is not None:
                            sources.append(read[0])
                            texts.append(read[1])
                            continue
                        if len(tools) > 1:
                            continue          # named alongside others: nothing readable here
                    getter = per_source.get(tool) or general(workspace_id)
                    per_tool.append(getter().pipeline.retrieve_passages(
                        question, org_id, about=about, workspace_id=workspace_id, viewer=viewer))
                except Exception:  # noqa: BLE001 - that tool adds nothing; the rest still read
                    if len(tools) == 1:
                        raise
                    logger.warning("Act: reading %s failed; using the other tools", tool, exc_info=True)
        finally:
            graph_plan.reset_plan(token)
        indexed_sources, indexed_text = indexed_context(
            _interleave(per_tool), org_id, viewer, workspace_id, days=days)
        return sources + indexed_sources, "\n\n".join(t for t in [*texts, indexed_text] if t)

    return search


def _interleave(lists: list[list]) -> list:
    """One hit from each tool in turn, each passage once, so a cap on the total
    still leaves every named tool its share."""
    out, seen = [], set()
    for row in itertools.zip_longest(*lists):
        for hit in row:
            key = None if hit is None else (hit.document_id, hit.chunk_index)
            if hit is not None and key not in seen:
                seen.add(key)
                out.append(hit)
    return out


def _require_on(org_id: str) -> ActionsSettings:
    """Act's routes exist for a company only once its admin allowed Act."""
    if not policy.act_on(org_id):
        raise HTTPException(status_code=404, detail="Agent mode is not switched on.")
    return ActionsSettings.from_env()


def _action_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="No such action.") from exc


def _retired_label(name: str) -> str:
    """A past action whose tool is gone (a built-in from before MCP, or a
    removed server): "linear.comment" reads "Linear: comment"."""
    if name.startswith("mcp:"):
        return f"Removed tool: {name.rsplit(':', 1)[-1].replace('_', ' ')}"
    app, _, op = name.partition(".")
    return f"{service.SOURCE_NAMES.get(app, app.title())}: {op.replace('_', ' ') or 'action'}"


def _card(row: ActionRow, exposure: Exposure | None = None, servers: dict | None = None) -> dict:
    """What the Act page shows. Never a token; source titles only (the person
    could read every one of them when they asked)."""
    if servers is not None and row.action.startswith("mcp:"):
        from ..actions.mcp import get_mcp_action

        cls = get_mcp_action(row.action, row.org_id, servers)
    else:
        cls = get_action(row.action, row.org_id)
    return {
        "id": row.id,
        "action": row.action,
        "label": cls.label if cls else _retired_label(row.action),
        "attribution": cls.attribution_for(row.payload) if cls else None,
        "provider": cls.provider if cls else None,
        "target": row.target,
        "payload": row.payload,
        # The drafted words as the person reads and edits them (a Slack post
        # without the line naming who asked), and whether they edited them.
        "draft": cls.editable(row.payload) if cls else {},
        "edited": row.original_payload is not None,
        "payload_hash": row.payload_hash,
        "status": row.status,
        "exposure": row.exposure,
        "acknowledged": row.acknowledged,
        "sources": [s.get("title") for s in row.sources if not s.get("public")],
        "audience_size": exposure.audience_size if exposure else None,
        "outsiders": exposure.outsiders if exposure else None,
        "result": row.result,
        "expires_at": row.expires_at.isoformat(),
        "created_at": row.created_at.isoformat(),
    }


# -- The Act page -------------------------------------------------------------


@router.get("/act/status")
def act_status(session: SessionClaims = Depends(get_session)):
    """What this person can do: the MCP tools they may draft with now, and their
    recent drafts. Signing in to each server is ``GET /act/mcp``."""
    if not policy.act_on(session.org_id):
        return {"enabled": False, "actions": [], "recent": []}
    from ..actions.mcp import mcp_actions
    from ..actions.mcp_store import list_servers

    # Read once for all twenty cards: one server read per card (each with its
    # whole tool list) took this page to ~9s.
    servers = {s.id: s for s in list_servers(session.org_id)}
    return {
        "enabled": True,
        "actions": [{"name": a.name, "label": a.label, "provider": a.provider}
                    for a in mcp_actions(session.org_id, session.user_id) if not a.lookup],
        "recent": [_card(r, servers=servers) for r in store.list_for_org(
            session.org_id, user_id=session.user_id, limit=20)],
    }


def _selectable_model(org_id: str, model: str) -> bool:
    from ..llm import catalog, org_model

    if catalog.is_selectable(model):
        return True
    own = org_model.get_org_model_summary(org_id)
    return bool(own and own["model"] == model)


_NO_TOOLS = ("This model can't use tools, so it can't run Agent mode. Nothing was sent. "
             "Pick another model in the model menu.")


def _cannot_call_tools(org_id: str, model: str) -> bool:
    """The company's own model failed Agent mode's test when it was saved
    (``llm_model._agent_probe``): said before drafting, never a request that
    quietly drafts nothing. Untested or unknown is allowed."""
    from ..llm import org_model

    own = org_model.get_org_model_summary(org_id)
    return bool(own and own["model"] == model and own.get("agent") == "none")


class ActRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    #: Act mode in a space's chat drafts from that space; a non-member is 403.
    workspace_id: uuid.UUID | None = None
    #: The chat's model picker. Checked like chat's: a catalogued model or
    #: this org's own, never an arbitrary id (it would spend our key).
    model: str | None = Field(default=None, max_length=200)


_MODEL_DOWN = ("The model is temporarily unavailable or experiencing high demand. "
               "Nothing was sent. Please try again in a few minutes.")
_MODEL_UNPAID = ("The model's API key is out of credit or over its quota. Nothing was sent. "
                 "An admin can add credit, or choose another model in Admin › Model.")


_MODEL_TOO_LARGE = ("This request is too large for the chosen model's plan. Nothing was sent. "
                    "Pick another model in the model menu, or ask an admin to turn off tools nobody uses.")
_MODEL_KEY = ("The model's API key was refused. Nothing was sent. "
              "An admin can check the key, or choose another model in Admin › Model.")


def _model_down(exc: LLMProviderError) -> str:
    """Say why, so the right person acts instead of everyone retrying: a
    request too large for the plan (Groq's 413: a model choice, not a wait),
    a refused key, or no credit. The provider's status code and words are
    the only signal; a 429 is the quota by our rules."""
    said = str(exc).lower()
    if "error code: 413" in said or "request too large" in said or "maximum number of items" in said:
        return _MODEL_TOO_LARGE
    if "error code: 401" in said or "error code: 403" in said:
        return _MODEL_KEY
    if isinstance(exc, LLMRateLimitError) or "credit balance" in said or "quota" in said:
        return _MODEL_UNPAID
    return _MODEL_DOWN


def _act_args(body: ActRequest, session: SessionClaims) -> dict:
    """Every check before drafting, raised as HTTP errors BEFORE any stream
    starts; then what ``service.propose`` takes."""
    settings = _require_on(session.org_id)
    check_rate_limit(f"act:{session.user_id}")
    user = get_user(session.user_id)
    viewer = viewer_for(session)
    if user is None or not user.email or viewer.is_unrestricted or not viewer.email:
        # An asker we cannot name does not act -- never "unrestricted".
        raise HTTPException(status_code=403, detail="Sign in again to use Agent mode.")
    if body.model and not _selectable_model(session.org_id, body.model):
        raise HTTPException(status_code=400, detail="Unknown model")
    if body.model and _cannot_call_tools(session.org_id, body.model):
        raise HTTPException(status_code=400, detail=_NO_TOOLS)
    workspace_id = str(body.workspace_id) if body.workspace_id else None
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return dict(question=body.question.strip(), org_id=session.org_id, user_id=session.user_id,
                email=user.email, viewer=viewer, llm=_llm(), embedder=_embedder(), vstore=_vstore(),
                settings=settings, scope_workspace_id=workspace_id, model=body.model,
                search=ask_search(session.user_id))


def _act_reply(outcome) -> dict:
    return {
        "reply": outcome.reply,
        "action": _card(outcome.action, outcome.exposure) if outcome.action else None,
        # Every draft this request made, each approved on its own.
        "actions": [_card(row, exposure) for row, exposure in outcome.drafts],
    }


@router.post("/act")
def act(body: ActRequest, session: SessionClaims = Depends(get_session)):
    args = _act_args(body, session)
    try:
        outcome = service.propose(args.pop("question"), **args)
    except LLMProviderError as exc:
        logger.warning("Act model unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=_model_down(exc)) from exc
    return _act_reply(outcome)


@router.post("/act/stream")
def act_stream(body: ActRequest, session: SessionClaims = Depends(get_session)):
    """``/act`` as a stream: the steps it takes while drafting ("Looking up in
    Linear: List teams", "Preparing a card: …"), as Ask shows its own, then the
    same reply as ``done``. The lines come from steps that ran, never the model."""
    from fastapi.responses import StreamingResponse

    from .chat import _sse_event, _with_progress

    args = _act_args(body, session)

    def run():
        try:
            yield _sse_event("done", _act_reply(service.propose(args.pop("question"), **args)))
        except LLMProviderError as exc:
            logger.warning("Act model unavailable: %s", exc)
            yield _sse_event("error", {"message": _model_down(exc), "status": 503})

    return StreamingResponse(_with_progress(run), media_type="text/event-stream")


class ConfirmRequest(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)
    acknowledged: bool = False
    #: False: the web chat asks ``/continue/stream`` next instead, because
    #: drafting the next step (lookups included) outlasts the web proxy.
    wait_for_next: bool = True


@router.post("/act/actions/{action_id}/confirm")
def confirm(action_id: str, body: ConfirmRequest, session: SessionClaims = Depends(get_session)):
    settings = _require_on(session.org_id)
    try:
        row = service.confirm(
            org_id=session.org_id, user_id=session.user_id,
            action_id=_action_id(action_id), shown_hash=body.payload_hash,
            acknowledged=body.acknowledged, settings=settings,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="No such action.") from exc
    except ActionExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ActionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (OAuthReauthRequiredError, ConfigurationError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ActionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"action": _card(row), "next": _continue(session, action_id) if body.wait_for_next else None}


@router.post("/act/actions/{action_id}/continue/stream")
def continue_stream(action_id: str, session: SessionClaims = Depends(get_session)):
    """What the request drafts next after this card was decided, streamed
    like ``/act/stream`` (its steps, then ``done``: an ``/act`` reply, or
    null when nothing continues)."""
    from fastapi.responses import StreamingResponse

    from .chat import _sse_event, _with_progress

    _require_on(session.org_id)
    return StreamingResponse(_with_progress(lambda: iter([_sse_event("done", _continue(session, action_id))])),
                             media_type="text/event-stream")


def _continue(session: SessionClaims, action_id: str) -> dict | None:
    """What a chain drafts next once the cards it waited on are decided, as
    ``/act`` replies; ``None`` when nothing continues. The approval itself has
    already run and is reported either way, so a model failure here is said,
    never an error for the approve click."""
    user, viewer = get_user(session.user_id), viewer_for(session)
    if user is None or not user.email or viewer.is_unrestricted or not viewer.email:
        return None
    try:
        got = service.continue_run(org_id=session.org_id, user_id=session.user_id, email=user.email,
                                   viewer_for=lambda surface: _surface_viewer(session, user.email, viewer, surface),
                                   action_id=_action_id(action_id),
                                   llm=_llm(), embedder=_embedder())
    except LLMProviderError as exc:
        logger.warning("Act continue: model unavailable: %s", exc)
        return {"reply": _model_down(exc), "action": None, "actions": []}
    return _act_reply(got[0]) if got else None


def _surface_viewer(session: SessionClaims, email: str, viewer, surface: dict):
    """Asked in the web chat: the person. Asked in Slack and approved here: as
    Slack decided it (a channel reads as the room), never wider."""
    if str(surface.get("kind", "web")).startswith("slack_"):
        from .slack_act import surface_viewer

        return surface_viewer(session.org_id, email, surface)
    return viewer


class EditRequest(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)
    #: Drafted words only; ``Action.edit`` refuses any other field.
    fields: dict[str, Annotated[str, Field(max_length=20000)]] = Field(max_length=2)


@router.patch("/act/actions/{action_id}")
def edit(action_id: str, body: EditRequest, session: SessionClaims = Depends(get_session)):
    """Save the person's edit of a pending draft; returns the new card and hash."""
    settings = _require_on(session.org_id)
    try:
        row = service.edit(
            org_id=session.org_id, user_id=session.user_id,
            action_id=_action_id(action_id), shown_hash=body.payload_hash,
            fields=body.fields, settings=settings,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="No such action.") from exc
    except ActionExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ActionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ActionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"action": _card(row)}


class ReviseRequest(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)
    instruction: str = Field(min_length=1, max_length=1000)
    model: str | None = Field(default=None, max_length=200)


@router.post("/act/actions/{action_id}/revise")
def revise(action_id: str, body: ReviseRequest, session: SessionClaims = Depends(get_session)):
    """Rework a pending draft's words as the person asks; returns the new card."""
    _require_on(session.org_id)
    check_rate_limit(f"act:{session.user_id}")
    if body.model and not _selectable_model(session.org_id, body.model):
        raise HTTPException(status_code=400, detail="Unknown model")
    try:
        row = service.revise(
            org_id=session.org_id, user_id=session.user_id,
            action_id=_action_id(action_id), shown_hash=body.payload_hash,
            instruction=body.instruction, llm=_llm(), model=body.model,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="No such action.") from exc
    except ActionExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ActionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LLMProviderError as exc:
        raise HTTPException(status_code=503, detail=(
            "The model is busy right now. Your draft is unchanged; try again, or edit it yourself."
        )) from exc
    except ActionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"action": _card(row)}


@router.post("/act/actions/{action_id}/reject")
def reject(action_id: str, wait_for_next: bool = True, session: SessionClaims = Depends(get_session)):
    _require_on(session.org_id)
    store.reject(session.org_id, session.user_id, _action_id(action_id))
    row = store.get(session.org_id, session.user_id, _action_id(action_id))
    if row is None:
        raise HTTPException(status_code=404, detail="No such action.")
    # A chain whose last pending card was cancelled continues with what was
    # approved (or says what it cannot do now).
    return {"action": _card(row), "next": _continue(session, action_id) if wait_for_next else None}


# -- Connecting your own account ----------------------------------------------


# -- Admin --------------------------------------------------------------------


@router.get("/admin/actions")
def admin_actions(session: SessionClaims = Depends(require_admin)):
    """The company's switches. No log: what a person drafted or sent is theirs
    (their Act page), and each tool already records what was created by whom."""
    settings = ActionsSettings.from_env()
    enabled = policy.enabled_actions(session.org_id, None)
    return {
        "enabled": policy.ACT_KEY in enabled,
        "block_wider": settings.block_wider,
        # Where Act may be asked from besides the web page: each Slack surface
        # is its own switch, because each is its own permission decision.
        "surfaces": [
            {"name": policy.surface_key(kind), "label": label,
             "enabled": policy.surface_key(kind) in enabled}
            for kind, label in policy.SURFACES.items()
        ],
    }


class PolicyRequest(BaseModel):
    enabled: bool


@router.put("/admin/actions/{name}")
def admin_set_action(name: str, body: PolicyRequest, session: SessionClaims = Depends(require_admin)):
    """Switch Agent mode itself (``act``) or one Slack surface. Which tools it
    may use is chosen per MCP server (``/admin/mcp``)."""
    surfaces = {policy.surface_key(kind) for kind in policy.SURFACES}
    if name not in surfaces and name != policy.ACT_KEY:
        raise HTTPException(status_code=404, detail="No such action")
    policy.set_policy(session.org_id, name, body.enabled, updated_by=session.user_id)
    return {"name": name, "enabled": body.enabled}


# -- A space's own switch ------------------------------------------------------


@router.get("/act/spaces/{workspace_id}")
def space_act(workspace_id: str, session: SessionClaims = Depends(get_session),
              role: str = Depends(get_workspace_role)):
    """Whether Act works in this space, for its members (the chat asks), and
    what its owner may change: only within what the company allows."""
    company = policy.act_on(session.org_id)
    return {
        "company": "on" if company else "off",
        "enabled": policy.act_on(session.org_id, workspace_id),
        "can_change": role == "owner" and company,
    }


@router.get("/act/spaces/{workspace_id}/tools")
def space_tools(workspace_id: str, session: SessionClaims = Depends(get_session),
                _owner: str = Depends(require_workspace_owner)):
    """What the admin switched on, and what the owner switched off here. Only
    tools the admin turned on are listed: an owner only narrows."""
    from ..actions.mcp_presets import GROUPS
    from ..actions.mcp_store import destructive, list_servers, tool_title

    off = policy.space_off(session.org_id, workspace_id)
    sources = policy.space_sources(session.org_id, workspace_id)
    out = []
    for server in list_servers(session.org_id):
        tools = [t for t in server.tools if server.mode(t.get("name", "")) != "off"]
        if not server.enabled or not tools or not policy.server_in_space(server, sources):
            continue
        preset = server.preset
        group = preset.group if preset else None
        out.append({
            "id": server.id, "name": server.name, "group": group,
            "group_name": GROUPS[group]["name"] if group else None,
            "brand": group or (preset.key if preset else None),
            "on": f"mcp:{server.id}" not in off,
            "tools": [{"name": t["name"], "title": tool_title(t, server.name),
                       "description": (t.get("description") or "")[:400],
                       "read_only": server.is_read(t), "destructive": destructive(t),
                       "on": not policy.off_in_space(off, server.id, t["name"])} for t in tools]})
    return {"servers": out}


class SpaceToolSwitch(BaseModel):
    server_id: uuid.UUID
    #: None switches the whole server.
    tool: str | None = Field(default=None, max_length=200)
    enabled: bool


@router.put("/act/spaces/{workspace_id}/tools")
def set_space_tool(workspace_id: str, body: SpaceToolSwitch,
                   session: SessionClaims = Depends(get_session),
                   _owner: str = Depends(require_workspace_owner)):
    """The owner switches a tool (or a whole server) off or back on for their
    space. "On" only undoes their own "off": the admin's choice is the ceiling."""
    from ..actions.mcp_store import get_server

    server = get_server(session.org_id, str(body.server_id))
    if server is None or (body.tool is not None and server.tool(body.tool) is None):
        raise HTTPException(status_code=404, detail="No such tool.")
    name = f"mcp:{server.id}" + (f":{body.tool}" if body.tool else "")
    policy.set_policy(session.org_id, name, body.enabled, workspace_id=workspace_id,
                      updated_by=session.user_id)
    return space_tools(workspace_id, session, "owner")


@router.put("/act/spaces/{workspace_id}")
def set_space_act(workspace_id: str, body: PolicyRequest,
                  session: SessionClaims = Depends(get_session),
                  _owner: str = Depends(require_workspace_owner)):
    """The space owner's switch. Narrows, never widens: with Act off for the
    company there is nothing to turn on."""
    if not policy.act_on(session.org_id):
        raise HTTPException(status_code=409, detail="Your admin hasn't turned on Agent mode for the company.")
    policy.set_policy(session.org_id, policy.ACT_KEY, body.enabled,
                      workspace_id=workspace_id, updated_by=session.user_id)
    return space_act(workspace_id, session, "owner")
