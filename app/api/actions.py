"""Act mode routes (docs/plans/2026-10-13-write-actions.md).

A separate surface from Ask and Chart: its own page, its own routes, its own
package. ``org_id`` and the person come from the session here, as everywhere;
an action id from the URL is only ever looked up together with both.

Connecting for actions is a PERSON's flow, not an admin's: the state row is
bound to whoever clicked (``user_id``), under its own provider value
(``linear_act``/``github_act``), so it can never complete a source connect
and a forwarded callback can only attach an account to the person who
started it.
"""

from __future__ import annotations

import itertools
import logging
import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..actions import ACTIONS, policy
from ..actions.factory import get_action
from ..actions import service, store
from ..actions.exposure import Exposure
from ..actions.drive import DRIVE_FILE
from ..actions.linear import WRITE_SCOPES, viewer as linear_viewer
from ..actions.store import ActionRow
from ..auth.factory import build_oauth_provider
from ..auth.github_oauth import GitHubAppProvider
from ..auth.google_oauth import GoogleOAuthProvider
from ..auth.linear_oauth import LinearOAuthProvider
from ..auth.notion_oauth import NotionOAuthProvider
from ..auth.slack_oauth import SlackOAuthProvider
from ..auth.oauth_state import consume_link_state, create_state
from ..auth.user_connections import (
    UserTokens,
    delete_user_connection,
    list_user_connections,
    save_user_connection,
)
from ..auth.users import get_user
from ..config.settings import (
    ActionsSettings,
    ApiSettings,
    GoogleSettings,
    NotionSettings,
    SlackSettings,
)
from ..core.exceptions import (
    ActionConflict,
    ActionError,
    ActionExpired,
    AuthError,
    ConfigurationError,
    LLMProviderError,
    NotFoundError,
    OAuthError,
    OAuthReauthRequiredError,
)
from ..db.connection import get_connection
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

#: Providers a person can connect for actions, and the state value of each.
ACT_PROVIDERS = {
    "linear": "linear_act", "github": "github_act",
    "notion": "notion_act", "google": "google_act", "slack": "slack_act",
}
ACT_STATES = {v: k for k, v in ACT_PROVIDERS.items()}
#: Where a person lands after connecting an account for Act: its section on
#: the Account page.
_PAGE = "/account"


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

    def search(question, about, *, org_id, workspace_id=None, tags=None, viewer=None):
        text = about or question
        if tags:  # a Slack channel: that channel only, as the room
            hits = per_source["slack"]().pipeline.retrieve_passages(
                question, org_id, about=about, workspace_id=workspace_id, tags=tags, viewer=viewer)
            return indexed_context(hits, org_id, viewer, workspace_id)

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
            _interleave(per_tool), org_id, viewer, workspace_id)
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


def _card(row: ActionRow, exposure: Exposure | None = None) -> dict:
    """What the Act page shows. Never a token; source titles only (the person
    could read every one of them when they asked)."""
    cls = get_action(row.action, row.org_id)
    return {
        "id": row.id,
        "action": row.action,
        "label": cls.label if cls else row.action,
        "attribution": cls.attribution_for(row.payload) if cls else None,
        "folder_choice": bool(cls and cls.provider == "google" and ActionsSettings.from_env().folder_picker),
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
    """What this person can do: switched-on actions and their own connections."""
    if not policy.act_on(session.org_id):
        return {"enabled": False, "actions": [], "connections": [], "providers": []}
    enabled = policy.enabled_actions(session.org_id, None)
    connections = {c.provider: c for c in list_user_connections(session.org_id, session.user_id)}
    providers = []
    for provider in ACT_PROVIDERS:
        conn = connections.get(provider)
        providers.append({
            "provider": provider,
            "needed": any(a.provider == provider for n, a in ACTIONS.items() if n in enabled),
            "connected": conn is not None and not conn.needs_reauth,
            "needs_reauth": bool(conn and conn.needs_reauth),
            "account": conn.external_user_name or conn.external_user_id if conn else None,
            "available": _available(provider),
        })
    return {
        "enabled": True,
        "actions": [
            {"name": n, "label": a.label, "provider": a.provider}
            for n, a in ACTIONS.items() if n in enabled
        ],
        "providers": providers,
        "recent": [_card(r) for r in store.list_for_org(
            session.org_id, user_id=session.user_id, limit=20)],
    }


def _selectable_model(org_id: str, model: str) -> bool:
    from ..llm import catalog, org_model

    if catalog.is_selectable(model):
        return True
    own = org_model.get_org_model_summary(org_id)
    return bool(own and own["model"] == model)


class ActRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    #: Act mode in a space's chat drafts from that space; a non-member is 403.
    workspace_id: uuid.UUID | None = None
    #: The chat's model picker. Checked like chat's: a catalogued model or
    #: this org's own, never an arbitrary id (it would spend our key).
    model: str | None = Field(default=None, max_length=200)


@router.post("/act")
def act(body: ActRequest, session: SessionClaims = Depends(get_session)):
    settings = _require_on(session.org_id)
    check_rate_limit(f"act:{session.user_id}")
    user = get_user(session.user_id)
    viewer = viewer_for(session)
    if user is None or not user.email or viewer.is_unrestricted or not viewer.email:
        # An asker we cannot name does not act -- never "unrestricted".
        raise HTTPException(status_code=403, detail="Sign in again to use Agent mode.")
    if body.model and not _selectable_model(session.org_id, body.model):
        raise HTTPException(status_code=400, detail="Unknown model")
    workspace_id = str(body.workspace_id) if body.workspace_id else None
    if workspace_id is not None:
        try:
            assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        outcome = service.propose(
            body.question.strip(),
            org_id=session.org_id, user_id=session.user_id, email=user.email,
            viewer=viewer, llm=_llm(), embedder=_embedder(), vstore=_vstore(),
            settings=settings, scope_workspace_id=workspace_id, model=body.model,
            search=ask_search(session.user_id),
        )
    except LLMProviderError as exc:
        logger.warning("Act model unavailable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=(
                "The model is temporarily unavailable or experiencing high demand. "
                "Nothing was sent. Please try again in a few minutes."
            ),
        ) from exc
    return {
        "reply": outcome.reply,
        "action": _card(outcome.action, outcome.exposure) if outcome.action else None,
        # Every draft this request made, each approved on its own.
        "actions": [_card(row, exposure) for row, exposure in outcome.drafts],
    }


class ConfirmRequest(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)
    acknowledged: bool = False


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
    return {"action": _card(row)}


class EditRequest(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)
    #: Drafted words only; ``Action.edit`` refuses any other field.
    fields: dict[str, Annotated[str, Field(max_length=20000)]] = Field(max_length=2)


class FolderRequest(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)
    folder_id: str = Field(min_length=10, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


@router.get("/act/google/picker")
def google_picker(session: SessionClaims = Depends(get_session)):
    """What Google's folder picker needs in the browser: the picker key, the
    project number, and the person's OWN drive.file token (theirs to use, and
    it reaches only files Handbook made or they picked). 404 when not set up."""
    settings = _require_on(session.org_id)
    if not settings.folder_picker:
        raise HTTPException(status_code=404, detail="Folder choice isn't set up.")
    from ..auth.user_connections import fresh_user_token

    try:
        token = fresh_user_token(session.org_id, session.user_id, "google")
    except (ConfigurationError, OAuthReauthRequiredError) as exc:
        raise HTTPException(status_code=409, detail="Connect your Google account under Account → Agent first.") from exc
    return {"api_key": settings.google_picker_api_key, "app_id": settings.google_picker_app_id,
            "token": token}


@router.post("/act/actions/{action_id}/folder")
def choose_folder(action_id: str, body: FolderRequest, session: SessionClaims = Depends(get_session)):
    """Put a pending Doc or Sheet into the folder the person picked in Google's window."""
    _require_on(session.org_id)
    try:
        row = service.choose_folder(org_id=session.org_id, user_id=session.user_id,
                                    action_id=_action_id(action_id), shown_hash=body.payload_hash,
                                    folder_id=body.folder_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="No such action.") from exc
    except ActionExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ActionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OAuthReauthRequiredError as exc:
        raise HTTPException(status_code=409, detail="Reconnect your Google account under Account → Agent.") from exc
    except ActionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"action": _card(row)}


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
def reject(action_id: str, session: SessionClaims = Depends(get_session)):
    _require_on(session.org_id)
    store.reject(session.org_id, session.user_id, _action_id(action_id))
    row = store.get(session.org_id, session.user_id, _action_id(action_id))
    if row is None:
        raise HTTPException(status_code=404, detail="No such action.")
    return {"action": _card(row)}


# -- Connecting your own account ----------------------------------------------


def _act_oauth(provider: str, settings: ActionsSettings | None = None):
    """The OAuth client a PERSON connects through for actions.

    Notion: the separate "Handbook Actions" connection, never the read one.
    Google: the same client, asking for ``drive.file`` alone. Linear,
    GitHub and Slack: their usual clients (Linear's write scopes and Slack's
    user scopes go in the URL).
    """
    from dataclasses import replace

    settings = settings or ActionsSettings.from_env()
    if provider == "notion":
        if not (settings.notion_client_id and settings.notion_client_secret
                and settings.notion_redirect_uri):
            raise ConfigurationError("Notion actions are not set up (NOTION_ACT_CLIENT_ID).")
        return NotionOAuthProvider(replace(
            NotionSettings.from_env(), client_id=settings.notion_client_id,
            client_secret=settings.notion_client_secret,
            redirect_uri=settings.notion_redirect_uri,
        ))
    if provider == "google":
        return GoogleOAuthProvider(replace(GoogleSettings.from_env(), scopes=DRIVE_FILE))
    return build_oauth_provider(provider)


def _available(provider: str) -> bool:
    try:
        _act_oauth(provider)
    except (OAuthError, ConfigurationError):
        return False
    return True


@router.get("/act/connect/{provider}")
def connect(provider: str, session: SessionClaims = Depends(get_session)):
    _require_on(session.org_id)
    if provider not in ACT_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    try:
        oauth = _act_oauth(provider)
    except (OAuthError, ConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = create_state(session.org_id, ACT_PROVIDERS[provider], user_id=session.user_id)
    if isinstance(oauth, LinearOAuthProvider):
        return RedirectResponse(url=oauth.authorize_url(state, scopes=WRITE_SCOPES))
    if isinstance(oauth, GoogleOAuthProvider):
        return RedirectResponse(url=oauth.authorize_url(state, include_granted=False))
    if isinstance(oauth, SlackOAuthProvider):
        return RedirectResponse(url=oauth.authorize_user_url(state))
    return RedirectResponse(url=oauth.authorize_url(state))


@router.delete("/act/connections/{provider}")
def disconnect(provider: str, session: SessionClaims = Depends(get_session)):
    if provider not in ACT_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    return {"deleted": delete_user_connection(session.org_id, session.user_id, provider)}


def _company_workspaces(org_id: str, provider: str) -> set[str]:
    """The tool workspaces the company connected, company-wide or in a space."""
    from ..auth.credentials import connected_anywhere

    return {ext for ext, _config in connected_anywhere(org_id, provider) if ext}


def _notion_owner(token: str) -> tuple[str, str | None]:
    """The person behind a Notion token: ``/users/me`` is the connection's bot,
    and the bot's owner is whoever authorized it."""
    import httpx

    from ..actions.notion import API, VERSION

    try:
        response = httpx.get(f"{API}/users/me", timeout=15.0, headers={
            "Authorization": f"Bearer {token}", "Notion-Version": VERSION})
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise OAuthError("Notion did not say who this account is", cause=exc) from exc
    owner = ((response.json() or {}).get("bot") or {}).get("owner") or {}
    user = owner.get("user") or {}
    if not user.get("id"):
        raise OAuthError("Notion did not say who authorized this connection")
    email = (user.get("person") or {}).get("email")
    return user["id"], user.get("name") or email


def finish_act_connect(code: str | None, state: str, settings: ApiSettings) -> RedirectResponse:
    """Complete "Connect your account for actions" from the provider's callback.

    The org and the PERSON come from the consumed state. The account must be
    the COMPANY's: a Linear, Notion or Slack workspace the company connected
    for reading (company-wide or in any space), and on Google and Slack the
    person's own work address. Anything else would
    let company content be written somewhere the company does not control.
    The code is exchanged with the Act client (``_act_oauth``), never the
    read connection's.
    """
    base = (settings.frontend_url or "").rstrip("/")
    provider = ACT_STATES.get(_peek_provider(state) or "", "")

    def back(query: str) -> RedirectResponse:
        return RedirectResponse(url=f"{base}{_PAGE}?{query}#act")

    try:
        org_id, user_id = consume_link_state(state, provider=ACT_PROVIDERS.get(provider, "?"))
        if not code:
            raise OAuthError("no authorization code")
        oauth = _act_oauth(provider)
        if provider == "linear" and isinstance(oauth, LinearOAuthProvider):
            tokens = oauth.exchange_code(code)
            who = linear_viewer(tokens.access_token)
            # Only the company's own Linear: company content written into a
            # personal workspace would be content leaving the company.
            if tokens.external_workspace_id not in _company_workspaces(org_id, "linear"):
                return back("connect_error=linear&reason=workspace")
            user_tokens = UserTokens(
                access_token=tokens.access_token, refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
                external_user_id=str(who["viewer"].get("id") or ""),
                external_user_name=who["viewer"].get("name") or who["viewer"].get("email"),
                scopes=tuple(WRITE_SCOPES.split(",")),
            )
        elif provider == "notion" and isinstance(oauth, NotionOAuthProvider):
            tokens = oauth.exchange_code(code)
            if tokens.external_workspace_id not in _company_workspaces(org_id, "notion"):
                return back("connect_error=notion&reason=workspace")
            person_id, person_name = _notion_owner(tokens.access_token)
            user_tokens = UserTokens(
                access_token=tokens.access_token, external_user_id=person_id,
                external_user_name=person_name,
                scopes=("insert_content", "insert_comments", "read_comments"),
            )
        elif provider == "google" and isinstance(oauth, GoogleOAuthProvider):
            tokens = oauth.exchange_code(code)
            if not _is_own_address(org_id, user_id, tokens.external_workspace_id):
                return back("connect_error=google&reason=account")
            user_tokens = UserTokens(
                access_token=tokens.access_token, refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at, external_user_id=tokens.external_workspace_id,
                external_user_name=tokens.external_workspace_name, scopes=(DRIVE_FILE,),
            )
        elif provider == "slack" and isinstance(oauth, SlackOAuthProvider):
            token, slack_user, team, granted = oauth.exchange_user_code(code)
            # Also the person's own work address: a company that connected no
            # Slack anywhere still knows its people by email.
            teams = _company_workspaces(org_id, "slack")
            if teams and team not in teams:
                return back("connect_error=slack&reason=workspace")
            from .slack_events import _slack_email

            email = _slack_email(token, slack_user)
            if not _is_own_address(org_id, user_id, email):
                return back("connect_error=slack&reason=account")
            # ponytail: assumes the Slack app has token rotation off (its
            # tokens never expire); with rotation on, store and refresh them.
            user_tokens = UserTokens(
                access_token=token, external_user_id=slack_user, external_user_name=email,
                scopes=granted or tuple(SlackSettings.from_env().act_user_scopes.split(",")),
            )
        elif provider == "github" and isinstance(oauth, GitHubAppProvider):
            tokens, login, name = oauth.exchange_user_code(code)
            user_tokens = UserTokens(
                access_token=tokens.access_token, refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at, external_user_id=login,
                external_user_name=name or login,
            )
        else:
            raise OAuthError("not an Act connect flow")
        if not user_tokens.external_user_id:
            raise OAuthError("the tool did not say who this account is")
        save_user_connection(org_id, user_id, provider, user_tokens)
    except (OAuthError, ActionError, ConfigurationError) as exc:
        logger.info("act: connect failed for %s: %s", provider or "?", exc)
        return back(f"connect_error={provider or 'unknown'}")
    return back(f"connected={provider}")


def _is_own_address(org_id: str, user_id: str, email: str | None) -> bool:
    """Is this Google or Slack account the person's work address (or a prior
    one)? Company content must never be written into a personal account."""
    from ..auth.email_change import aliases_for

    user = get_user(user_id)
    if user is None or not user.email or not email:
        return False
    own = {user.email.lower(), *(a.lower() for a in aliases_for(org_id, user.email))}
    return email.lower() in own


def _peek_provider(state: str) -> str | None:
    from ..auth.oauth_state import peek_state_provider

    return peek_state_provider(state)


def is_act_state(state: str | None) -> bool:
    """Routing only: does this callback belong to an Act connect flow?"""
    return bool(state) and (_peek_provider(state) or "") in ACT_STATES


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
        "catalogue": [
            {"name": n, "label": a.label, "provider": a.provider, "enabled": n in enabled}
            for n, a in ACTIONS.items()
        ],
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
    """Switch Act itself (``act``), one action, or one Slack surface."""
    surfaces = {policy.surface_key(kind) for kind in policy.SURFACES}
    if name not in ACTIONS and name not in surfaces and name != policy.ACT_KEY:
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
