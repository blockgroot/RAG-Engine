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

import logging
import uuid
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..actions import ACTIONS, policy
from ..actions import service, store
from ..actions.exposure import Exposure
from ..actions.linear import WRITE_SCOPES, viewer as linear_viewer
from ..actions.store import ActionRow
from ..auth.factory import build_oauth_provider
from ..auth.github_oauth import GitHubAppProvider
from ..auth.linear_oauth import LinearOAuthProvider
from ..auth.oauth_state import consume_link_state, create_state
from ..auth.user_connections import (
    UserTokens,
    delete_user_connection,
    list_user_connections,
    save_user_connection,
)
from ..auth.users import get_user
from ..config.settings import ActionsSettings, ApiSettings
from ..core.exceptions import (
    ActionConflict,
    ActionError,
    ActionExpired,
    ConfigurationError,
    NotFoundError,
    OAuthError,
    OAuthReauthRequiredError,
)
from ..db.connection import get_connection
from ..security.rate_limit import check_rate_limit
from .deps import SessionClaims, get_session, require_admin, viewer_for

logger = logging.getLogger("actions")

router = APIRouter(tags=["act"])

#: Providers a person can connect for actions, and the state value of each.
ACT_PROVIDERS = {"linear": "linear_act", "github": "github_act"}
ACT_STATES = {v: k for k, v in ACT_PROVIDERS.items()}
_PAGE = "/act"


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


def _require_on() -> ActionsSettings:
    settings = ActionsSettings.from_env()
    if not settings.enabled:
        raise HTTPException(status_code=404, detail="Act is not switched on.")
    return settings


def _action_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="No such action.") from exc


def _card(row: ActionRow, exposure: Exposure | None = None) -> dict:
    """What the Act page shows. Never a token; source titles only (the person
    could read every one of them when they asked)."""
    cls = ACTIONS.get(row.action)
    return {
        "id": row.id,
        "action": row.action,
        "label": cls.label if cls else row.action,
        "provider": cls.provider if cls else None,
        "target": row.target,
        "payload": row.payload,
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
    settings = ActionsSettings.from_env()
    if not settings.enabled:
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


class ActRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


@router.post("/act")
def act(body: ActRequest, session: SessionClaims = Depends(get_session)):
    settings = _require_on()
    check_rate_limit(f"act:{session.user_id}")
    user = get_user(session.user_id)
    viewer = viewer_for(session)
    if user is None or not user.email or viewer.is_unrestricted or not viewer.email:
        # An asker we cannot name does not act -- never "unrestricted".
        raise HTTPException(status_code=403, detail="Sign in again to use Act.")
    outcome = service.propose(
        body.question.strip(),
        org_id=session.org_id, user_id=session.user_id, email=user.email,
        viewer=viewer, llm=_llm(), embedder=_embedder(), vstore=_vstore(),
        settings=settings,
    )
    return {
        "reply": outcome.reply,
        "action": _card(outcome.action, outcome.exposure) if outcome.action else None,
    }


class ConfirmRequest(BaseModel):
    payload_hash: str = Field(min_length=64, max_length=64)
    acknowledged: bool = False


@router.post("/act/actions/{action_id}/confirm")
def confirm(action_id: str, body: ConfirmRequest, session: SessionClaims = Depends(get_session)):
    settings = _require_on()
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


@router.post("/act/actions/{action_id}/reject")
def reject(action_id: str, session: SessionClaims = Depends(get_session)):
    _require_on()
    store.reject(session.org_id, session.user_id, _action_id(action_id))
    row = store.get(session.org_id, session.user_id, _action_id(action_id))
    if row is None:
        raise HTTPException(status_code=404, detail="No such action.")
    return {"action": _card(row)}


# -- Connecting your own account ----------------------------------------------


@router.get("/act/connect/{provider}")
def connect(provider: str, session: SessionClaims = Depends(get_session)):
    _require_on()
    if provider not in ACT_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    try:
        oauth = build_oauth_provider(provider)
    except (OAuthError, ConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = create_state(session.org_id, ACT_PROVIDERS[provider], user_id=session.user_id)
    if isinstance(oauth, LinearOAuthProvider):
        return RedirectResponse(url=oauth.authorize_url(state, scopes=WRITE_SCOPES))
    return RedirectResponse(url=oauth.authorize_url(state))


@router.delete("/act/connections/{provider}")
def disconnect(provider: str, session: SessionClaims = Depends(get_session)):
    if provider not in ACT_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")
    return {"deleted": delete_user_connection(session.org_id, session.user_id, provider)}


def _org_linear_workspace(org_id: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT external_workspace_id FROM oauth_connections "
            "WHERE org_id = %s::uuid AND provider = 'linear' AND workspace_id IS NULL",
            (org_id,),
        ).fetchone()
    return row[0] if row else None


def finish_act_connect(oauth, code: str | None, state: str, settings: ApiSettings) -> RedirectResponse:
    """Complete "Connect your account for actions" from the provider's callback.

    The org and the PERSON come from the consumed state. A Linear account from
    a different Linear workspace than the company's is refused: its teams are
    not this company's teams.
    """
    base = (settings.frontend_url or "").rstrip("/")
    provider = ACT_STATES.get(_peek_provider(state) or "", "")

    def back(query: str) -> RedirectResponse:
        return RedirectResponse(url=f"{base}{_PAGE}?{query}")

    try:
        org_id, user_id = consume_link_state(state, provider=ACT_PROVIDERS.get(provider, "?"))
        if not code:
            raise OAuthError("no authorization code")
        if provider == "linear" and isinstance(oauth, LinearOAuthProvider):
            tokens = oauth.exchange_code(code)
            who = linear_viewer(tokens.access_token)
            company = _org_linear_workspace(org_id)
            if company and company != tokens.external_workspace_id:
                return back("connect_error=linear&reason=workspace")
            user_tokens = UserTokens(
                access_token=tokens.access_token, refresh_token=tokens.refresh_token,
                expires_at=tokens.expires_at,
                external_user_id=str(who["viewer"].get("id") or ""),
                external_user_name=who["viewer"].get("name") or who["viewer"].get("email"),
                scopes=tuple(WRITE_SCOPES.split(",")),
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
    except (OAuthError, ActionError) as exc:
        logger.info("act: connect failed for %s: %s", provider or "?", exc)
        return back(f"connect_error={provider or 'unknown'}")
    return back(f"connected={provider}")


def _peek_provider(state: str) -> str | None:
    from ..auth.oauth_state import peek_state_provider

    return peek_state_provider(state)


def is_act_state(state: str | None) -> bool:
    """Routing only: does this callback belong to an Act connect flow?"""
    return bool(state) and (_peek_provider(state) or "") in ACT_STATES


# -- Admin --------------------------------------------------------------------


@router.get("/admin/actions")
def admin_actions(session: SessionClaims = Depends(require_admin), user: str | None = None):
    settings = ActionsSettings.from_env()
    enabled = policy.enabled_actions(session.org_id, None) if settings.enabled else set()
    user = _action_id(user) if user else None
    log = store.list_for_org(session.org_id, user_id=user, limit=100) if settings.enabled else []
    emails = {}
    ids = {r.user_id for r in log if r.user_id}
    if ids:
        with get_connection() as conn:
            emails = dict(conn.execute(
                "SELECT id::text, email FROM users WHERE org_id = %s::uuid AND id = ANY(%s::uuid[])",
                (session.org_id, list(ids)),
            ).fetchall())
    return {
        "enabled": settings.enabled,
        "block_wider": settings.block_wider,
        "catalogue": [
            {"name": n, "label": a.label, "provider": a.provider, "enabled": n in enabled}
            for n, a in ACTIONS.items()
        ],
        "log": [{**_card(r), "user_email": emails.get(r.user_id)} for r in log],
    }


class PolicyRequest(BaseModel):
    enabled: bool


@router.put("/admin/actions/{name}")
def admin_set_action(name: str, body: PolicyRequest, session: SessionClaims = Depends(require_admin)):
    _require_on()
    if name not in ACTIONS:
        raise HTTPException(status_code=404, detail="No such action")
    policy.set_policy(session.org_id, name, body.enabled, updated_by=session.user_id)
    return {"name": name, "enabled": body.enabled}
