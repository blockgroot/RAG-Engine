"""Other tools through MCP: an admin adds servers and chooses each tool's mode;
each person signs in to a server themselves (docs/plans/2026-10-21-mcp-actions.md).

Admin routes need an org admin; a person's routes act only on their own
sign-in. A sign-in's ``state`` is single-use, bound to the person who started
it, and carries the PKCE verifier (encrypted), so a captured callback is useless.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..actions import mcp_client, mcp_store, policy
from ..actions.mcp_presets import PRESETS
from ..auth.oauth_state import create_state
from ..auth.session import SessionClaims
from ..auth.user_connections import (
    UserTokens,
    delete_user_connection,
    fresh_user_token,
    list_user_connections,
    save_user_connection,
)
from ..config.settings import ActionsSettings, ApiSettings
from ..core.exceptions import ActionError, ConfigurationError, OAuthError, ProviderError
from ..db.connection import get_connection
from ..security import decrypt, encrypt
from .deps import get_session, require_admin

logger = logging.getLogger(__name__)
router = APIRouter(tags=["actions"])


def _server_id(raw: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="No such server.") from exc


def _server(org_id: str, raw: str) -> mcp_store.McpServer:
    server = mcp_store.get_server(org_id, _server_id(raw))
    if server is None:
        raise HTTPException(status_code=404, detail="No such server.")
    return server


def _admin_view(server: mcp_store.McpServer) -> dict:
    return {"id": server.id, "name": server.name, "url": server.url, "auth": server.auth,
            "enabled": server.enabled,
            "tools": [mcp_store.tool_view(server, t) for t in server.tools]}


def _list_tools(server_url: str, token: str | None) -> list[dict]:
    session = mcp_client.McpSession(server_url, token, timeout=ActionsSettings.from_env().mcp_timeout_seconds)
    session.open()
    tools, _cut = session.list_tools()
    return tools


# -- Admin ------------------------------------------------------------------------------


@router.get("/admin/mcp")
def admin_mcp(session: SessionClaims = Depends(require_admin)):
    settings = ActionsSettings.from_env()
    servers = mcp_store.list_servers(session.org_id)
    added = {s.url.rstrip("/") for s in servers}
    return {"servers": [_admin_view(s) for s in servers],
            "presets": [{"key": p.key, "name": p.name, "url": p.url, "note": p.note,
                         "added": p.url.rstrip("/") in added} for p in PRESETS.values()],
            "sign_in_ready": bool(settings.mcp_redirect_uri)}


class AddServer(BaseModel):
    #: One of the connectors' presets, or a name and address for any other server.
    preset: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=60)
    url: str | None = Field(default=None, min_length=8, max_length=500)


@router.post("/admin/mcp")
def admin_add_mcp(body: AddServer, session: SessionClaims = Depends(require_admin)):
    """Add a server. Its tools all start OFF. One that needs sign-in lists its
    tools once the admin signs in themselves and refreshes."""
    settings = ActionsSettings.from_env()
    preset = PRESETS.get(body.preset or "")
    if body.preset and preset is None:
        raise HTTPException(status_code=404, detail="No such connector.")
    if preset is None and not (body.name and body.url):
        raise HTTPException(status_code=400, detail="Pick a connector, or give a name and an address.")
    name, url = (preset.name, preset.url) if preset else (body.name.strip(), body.url.strip())
    if any(s.url.rstrip("/") == url.rstrip("/") for s in mcp_store.list_servers(session.org_id)):
        raise HTTPException(status_code=409, detail="That server is already added.")
    try:
        oauth = mcp_client.discover(url, settings.mcp_redirect_uri or "",
                                    timeout=settings.mcp_timeout_seconds, preset=preset)
        if oauth is not None and not settings.mcp_redirect_uri:
            raise HTTPException(status_code=409, detail="Set MCP_REDIRECT_URI or FRONTEND_URL so people can sign in.")
        tools = [] if oauth else _list_tools(url, None)
    except ActionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    server = mcp_store.create_server(session.org_id, name=name, url=url,
                                     auth="oauth" if oauth else "none", oauth=oauth, tools=tools,
                                     created_by=session.user_id)
    return _admin_view(server)


@router.post("/admin/mcp/{server_id}/refresh")
def admin_refresh_mcp(server_id: str, session: SessionClaims = Depends(require_admin)):
    """Read the tool list again. A tool that changed is OFF until chosen again."""
    server = _server(session.org_id, server_id)
    token = None
    if server.auth == "oauth":
        try:
            token = fresh_user_token(session.org_id, session.user_id, server.provider)
        except (ConfigurationError, OAuthError) as exc:
            raise HTTPException(status_code=409, detail=(
                f"Sign in to {server.name} yourself first (Account → Agent), then refresh.")) from exc
    try:
        tools = _list_tools(server.url, token)
    except ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mcp_store.set_tools(session.org_id, server.id, tools)
    return _admin_view(mcp_store.get_server(session.org_id, server.id))


class ToolMode(BaseModel):
    mode: str


@router.put("/admin/mcp/{server_id}/tools/{tool}")
def admin_tool_mode(server_id: str, tool: str, body: ToolMode, session: SessionClaims = Depends(require_admin)):
    server = _server(session.org_id, server_id)
    found = server.tool(tool)
    if found is None:
        raise HTTPException(status_code=404, detail="No such tool.")
    if body.mode not in mcp_store.MODES:
        raise HTTPException(status_code=400, detail="Mode is off, lookup or write.")
    if body.mode != "off" and not server.permitted(found):
        raise HTTPException(status_code=400, detail="Handbook never turns this tool on: it deletes or "
                            "changes things outside what Agent mode may do.")
    if body.mode == "lookup" and not server.is_read(found):
        raise HTTPException(status_code=400, detail=(
            "Only a tool that only reads can run without a card. Use write instead."))
    mcp_store.set_mode(session.org_id, server, tool, body.mode)
    return mcp_store.tool_view(mcp_store.get_server(session.org_id, server.id), found)


class BulkModes(BaseModel):
    #: "recommended": every permitted read as a lookup and every permitted write
    #: as a write (each still approved on a card); "off": everything off.
    choice: str = Field(pattern="^(recommended|off)$")


@router.put("/admin/mcp/{server_id}/tools")
def admin_bulk_modes(server_id: str, body: BulkModes, session: SessionClaims = Depends(require_admin)):
    server = _server(session.org_id, server_id)
    for tool in server.tools:
        mode = "off" if body.choice == "off" or not server.permitted(tool) else (
            "lookup" if server.is_read(tool) else "write")
        mcp_store.set_mode(session.org_id, server, tool["name"], mode)
        server = mcp_store.get_server(session.org_id, server.id)
    return _admin_view(server)


class ServerSwitch(BaseModel):
    enabled: bool


@router.put("/admin/mcp/{server_id}")
def admin_switch_mcp(server_id: str, body: ServerSwitch, session: SessionClaims = Depends(require_admin)):
    server = _server(session.org_id, server_id)
    mcp_store.set_enabled(session.org_id, server.id, body.enabled)
    return {"id": server.id, "enabled": body.enabled}


@router.delete("/admin/mcp/{server_id}")
def admin_delete_mcp(server_id: str, session: SessionClaims = Depends(require_admin)):
    server = _server(session.org_id, server_id)
    return {"deleted": mcp_store.delete_server(session.org_id, server.id)}


# -- A person's sign-in ------------------------------------------------------------------


@router.get("/act/mcp")
def my_mcp(session: SessionClaims = Depends(get_session)):
    """Servers this person may use, and whether they signed in. Admins also see
    servers with no tools on yet, so they can sign in and list them."""
    if not policy.act_on(session.org_id):
        return {"servers": []}
    mine = {c.provider: c for c in list_user_connections(session.org_id, session.user_id)}
    out = []
    for server in mcp_store.list_servers(session.org_id):
        on = [t["name"] for t in server.tools if server.mode(t["name"]) != "off"]
        if not server.enabled or (not on and session.role != "admin"):
            continue
        conn = mine.get(server.provider)
        out.append({"id": server.id, "name": server.name, "auth": server.auth, "tools_on": len(on),
                    "connected": server.auth == "none" or bool(conn and not conn.needs_reauth),
                    "needs_reauth": bool(conn and conn.needs_reauth)})
    return {"servers": out}


@router.get("/act/mcp/{server_id}/connect")
def connect_mcp(server_id: str, session: SessionClaims = Depends(get_session)):
    settings = ActionsSettings.from_env()
    server = _server(session.org_id, server_id)
    if server.auth != "oauth" or not server.oauth or not settings.mcp_redirect_uri:
        raise HTTPException(status_code=409, detail="This server doesn't use sign-in.")
    verifier, challenge = mcp_client.pkce()
    state = create_state(session.org_id, server.provider, user_id=session.user_id)
    with get_connection() as conn:
        conn.execute("UPDATE oauth_states SET code_verifier = %s WHERE state = %s",
                     (encrypt(verifier), state))
    return RedirectResponse(url=mcp_client.authorize_url(
        server.oauth, state=state, challenge=challenge, redirect_uri=settings.mcp_redirect_uri))


def _consume(state: str) -> tuple[str, str, str, str]:
    """``(org_id, user_id, provider, verifier)``; single use, unexpired, person-bound."""
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE oauth_states SET consumed_at = now() WHERE state = %s AND provider LIKE 'mcp:%%' "
            "AND consumed_at IS NULL AND expires_at > now() AND user_id IS NOT NULL "
            "AND code_verifier IS NOT NULL "
            "RETURNING org_id::text, user_id::text, provider, code_verifier", (state,)).fetchone()
    if not row:
        raise OAuthError("Invalid, expired, or already-used sign-in")
    return row[0], row[1], row[2], decrypt(row[3])


@router.get("/act/mcp/callback")
def mcp_callback(state: str = "", code: str | None = None):
    base = (ApiSettings.from_env().frontend_url or "").rstrip("/")
    try:
        org_id, user_id, provider, verifier = _consume(state)
        server = mcp_store.get_server(org_id, provider.split(":", 1)[1])
        if not code or server is None or not server.oauth:
            raise OAuthError("no code or no server")
        tokens = mcp_client.exchange(server.oauth, code=code, verifier=verifier,
                                     redirect_uri=ActionsSettings.from_env().mcp_redirect_uri or "")
        preset = server.preset
        if preset and preset.account_check:
            from ..auth.users import get_user

            user = get_user(user_id)
            # Only the company's own workspace: company content written into a
            # personal one would be content leaving the company.
            if not (user and user.email and preset.account_check(org_id, user.email, tokens.access_token)):
                return RedirectResponse(url=f"{base}/account?connect_error=mcp&reason=workspace#act")
        save_user_connection(org_id, user_id, provider, UserTokens(
            access_token=tokens.access_token, refresh_token=tokens.refresh_token,
            expires_at=tokens.expires_at, external_user_id=provider, external_user_name=server.name))
        if not server.tools:
            # The first sign-in lists the tools (all off), so the admin need
            # not come back and press Refresh. Never fails the sign-in.
            try:
                mcp_store.set_tools(org_id, server.id, _list_tools(server.url, tokens.access_token))
            except ProviderError as exc:
                logger.info("mcp: listing tools after sign-in failed: %s", exc)
    except (OAuthError, ProviderError) as exc:
        logger.info("mcp: sign-in failed: %s", exc)
        return RedirectResponse(url=f"{base}/account?connect_error=mcp#act")
    return RedirectResponse(url=f"{base}/account?connected=mcp#act")


@router.delete("/act/mcp/{server_id}/connection")
def disconnect_mcp(server_id: str, session: SessionClaims = Depends(get_session)):
    server = _server(session.org_id, server_id)
    return {"deleted": delete_user_connection(session.org_id, session.user_id, server.provider)}
