"""A small MCP client: JSON-RPC 2.0 over Streamable HTTP, and MCP's sign-in.

Only what Handbook uses: open a session, list the tools (bounded), call one.
No SDK: the protocol is a few POSTs, and the SDK would add a server stack we
do not run. Every address is an admin's input or came from a server's
metadata, so each is checked to be public HTTPS before it is fetched
(``public_https``): a server must never be a way into our own network.

Sign-in follows the MCP authorization spec: the server names its
authorization server (protected-resource metadata), Handbook registers itself
there once per server (dynamic client registration), and each person signs in
with PKCE. The ``resource`` parameter binds every token to that one server.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import secrets
import socket
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from ..auth.base import OAuthTokens, compute_expires_at
from ..core.exceptions import (
    ActionError,
    ActionOutcomeUnknown,
    ActionRejected,
    OAuthError,
    OAuthReauthRequiredError,
)

PROTOCOL_VERSION = "2025-06-18"
#: Bound the walk: a server listing thousands of tools is not one to offer.
MAX_TOOL_PAGES = 10
MAX_TOOLS = 200


class McpUnauthorized(OAuthReauthRequiredError):
    """The server wants a (fresh) sign-in. ``challenge`` is its WWW-Authenticate."""

    def __init__(self, message: str, *, challenge: str = "") -> None:
        super().__init__(message)
        self.challenge = challenge


def public_https(url: str) -> str:
    """``url`` if it is https to a public address, else ``ActionRejected``."""
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or not parsed.hostname:
        raise ActionRejected("An MCP server address must start with https://.")
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except OSError as exc:
        raise ActionRejected(f"Can't find {parsed.hostname}.", cause=exc) from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise ActionRejected("That address is not on the public internet.")
    return url


def _reason(response) -> str:
    """The server's own short reason for an error ("API not enabled…"), so an
    admin can act on it. Outside text: one line, bounded, never a link."""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        said = (error.get("message") if isinstance(error, dict) else error) or ""
    except ValueError:
        said = response.text or ""
    said = re.sub(r"https?://\S+", "", " ".join(str(said).split()))[:200].strip()
    return f": {said}" if said else ""


class McpSession:
    """One session with one server, as one person (``token``) or anonymously."""

    def __init__(self, url: str, token: str | None, *, timeout: float = 20.0) -> None:
        self.url = public_https(url)
        self.token = token
        self.timeout = timeout
        self.session_id: str | None = None
        self._next = 0

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json",
                   "MCP-Protocol-Version": PROTOCOL_VERSION}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _send(self, method: str, params: dict | None = None, *, notify: bool = False,
              write: bool = False) -> dict:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            self._next += 1
            body["id"] = self._next
        try:
            response = httpx.post(self.url, json=body, headers=self._headers(), timeout=self.timeout)
        except httpx.TimeoutException as exc:
            # A write that got no answer may have happened: unknown, never retried.
            if write:
                raise ActionOutcomeUnknown("The tool did not answer.", cause=exc) from exc
            raise ActionError("The tool did not answer.", cause=exc) from exc
        except httpx.HTTPError as exc:
            raise ActionError("Could not reach the tool.", cause=exc) from exc
        if response.status_code == 401:
            raise McpUnauthorized("Sign in to this tool again.",
                                  challenge=response.headers.get("www-authenticate", ""))
        if response.status_code == 403:
            raise ActionError("The tool refused: your account isn't allowed to do that.")
        if response.status_code >= 400:
            raise ActionError(f"The tool answered {response.status_code}{_reason(response)}.")
        if response.headers.get("mcp-session-id"):
            self.session_id = response.headers["mcp-session-id"]
        if notify:
            return {}
        message = _reply(response, body["id"])
        if message.get("error"):
            raise ActionError(f"The tool refused: {str(message['error'].get('message') or '')[:300]}")
        return message.get("result") or {}

    def open(self) -> dict:
        result = self._send("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                           "clientInfo": {"name": "Handbook", "version": "1"}})
        self._send("notifications/initialized", notify=True)
        return result

    def list_tools(self) -> tuple[list[dict], bool]:
        """Every tool, and whether the list was cut short (said, never hidden)."""
        tools: list[dict] = []
        cursor = None
        for _ in range(MAX_TOOL_PAGES):
            page = self._send("tools/list", {"cursor": cursor} if cursor else {})
            tools += [t for t in page.get("tools") or [] if isinstance(t, dict) and t.get("name")]
            cursor = page.get("nextCursor")
            if not cursor or len(tools) >= MAX_TOOLS:
                break
        return tools[:MAX_TOOLS], bool(cursor) or len(tools) > MAX_TOOLS

    def call_tool(self, name: str, arguments: dict, *, write: bool) -> dict:
        """Run one tool. For a write, no answer after sending is ``unknown``,
        never retried: it may have happened."""
        return self._send("tools/call", {"name": name, "arguments": arguments}, write=write)


def _reply(response: httpx.Response, want: int) -> dict:
    """The JSON-RPC answer to request ``want``, from JSON or an event stream."""
    if "text/event-stream" not in response.headers.get("content-type", ""):
        try:
            return response.json() or {}
        except ValueError as exc:
            raise ActionError("The tool sent an unreadable answer.", cause=exc) from exc
    for block in response.text.split("\n\n"):
        data = "\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith("data:"))
        if not data:
            continue
        try:
            message = json.loads(data)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == want:
            return message
    raise ActionError("The tool's answer never arrived.")


def result_text(result: dict, limit: int = 4000) -> str:
    """A tool result's text content, joined and bounded (truncation said)."""
    parts = [c.get("text") or "" for c in result.get("content") or [] if c.get("type") == "text"]
    if not parts and result.get("structuredContent") is not None:
        parts = [json.dumps(result["structuredContent"])[:limit]]
    text = "\n".join(parts)
    return text if len(text) <= limit else text[:limit] + "\n[…cut short]"


# -- Sign-in ---------------------------------------------------------------------------


def result_json(result: dict) -> dict:
    """A tool answer whose text is JSON (Linear's, Google's), as a dict; ``{}`` otherwise."""
    text = "".join(c.get("text", "") for c in result.get("content") or [] if isinstance(c, dict))
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _get_json(url: str, timeout: float = 10.0) -> dict | None:
    public_https(url)
    try:
        response = httpx.get(url, timeout=timeout, headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _well_known(base: str, name: str) -> list[str]:
    """RFC 8414 / 9728 locations: path-inserted first, then the root."""
    parsed = urlparse(base)
    root = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path.rstrip("/")
    return [f"{root}/.well-known/{name}{path}", f"{root}/.well-known/{name}"] if path else [
        f"{root}/.well-known/{name}"]


def discover(url: str, redirect_uri: str, *, timeout: float = 20.0, preset=None) -> dict | None:
    """How people sign in to this server, registered for Handbook; ``None``
    when it needs no sign-in. Raises ``ActionRejected`` with the reason.

    A ``preset`` (``mcp_presets``) supplies what a server does not: Handbook's
    own app there when it cannot register Handbook (GitHub, Slack), or the
    whole sign-in when it advertises none (Google)."""
    if preset is not None and preset.oauth:
        client_id, _secret = preset.client() if preset.client else (None, None)
        if not client_id:
            raise ActionRejected(f"{preset.name} needs Handbook's {preset.name} app configured first.")
        return {**preset.oauth, "client_id": client_id, "client_secret": None, "preset": preset.key,
                "resource": None}
    try:
        McpSession(url, None, timeout=timeout).open()
        return None
    except McpUnauthorized as exc:
        challenge = exc.challenge
    except ActionError as exc:
        raise ActionRejected(f"That doesn't answer like an MCP server ({exc}).") from exc

    hint = re.search(r'resource_metadata="([^"]+)"', challenge or "")
    resource = None
    for candidate in ([hint.group(1)] if hint else []) + _well_known(url, "oauth-protected-resource"):
        resource = _get_json(candidate)
        if resource:
            break
    servers = (resource or {}).get("authorization_servers") or []
    if not servers:
        raise ActionRejected("The server asks for sign-in but doesn't say where.")
    issuer = servers[0]
    meta = None
    for candidate in _well_known(issuer, "oauth-authorization-server") + _well_known(
            issuer, "openid-configuration"):
        meta = _get_json(candidate)
        if meta:
            break
    if not meta or not meta.get("authorization_endpoint") or not meta.get("token_endpoint"):
        raise ActionRejected("The server's sign-in details are incomplete.")
    if "S256" not in (meta.get("code_challenge_methods_supported") or ["S256"]):
        raise ActionRejected("The server's sign-in doesn't support PKCE, so Handbook won't use it.")
    for endpoint in (meta["authorization_endpoint"], meta["token_endpoint"]):
        public_https(endpoint)
    scopes = (resource or {}).get("scopes_supported") or []
    if preset is not None:
        scopes = preset.pick_scopes(scopes)
    if preset is not None and preset.client and not meta.get("registration_endpoint"):
        client_id, _secret = preset.client()
        if not client_id:
            raise ActionRejected(f"{preset.name} needs Handbook's {preset.name} app configured first.")
        # Handbook's own app there; its secret stays in settings, never copied here.
        return {"authorization_endpoint": meta["authorization_endpoint"],
                "token_endpoint": meta["token_endpoint"], "client_id": client_id,
                "client_secret": None, "preset": preset.key, "scope": " ".join(scopes) or None,
                "resource": url}
    if not meta.get("registration_endpoint"):
        raise ActionRejected("The server doesn't let apps register themselves, so it can't be added yet.")
    public_https(meta["registration_endpoint"])
    try:
        registered = httpx.post(meta["registration_endpoint"], timeout=timeout, json={
            "client_name": "Handbook", "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post"})
        client = registered.json() if registered.status_code < 400 else {}
    except (httpx.HTTPError, ValueError) as exc:
        raise ActionRejected("Registering Handbook with the server failed.", cause=exc) from exc
    if not client.get("client_id"):
        raise ActionRejected("Registering Handbook with the server failed.")
    from ..security import encrypt

    return {
        "authorization_endpoint": meta["authorization_endpoint"],
        "token_endpoint": meta["token_endpoint"],
        "client_id": client["client_id"],
        "client_secret": encrypt(client["client_secret"]) if client.get("client_secret") else None,
        "scope": " ".join(scopes) or None,
        "resource": url,
    }


def pkce() -> tuple[str, str]:
    """``(verifier, S256 challenge)``."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_url(oauth: dict, *, state: str, challenge: str, redirect_uri: str) -> str:
    params = {"response_type": "code", "client_id": oauth["client_id"], "redirect_uri": redirect_uri,
              "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    if oauth.get("resource"):
        params["resource"] = oauth["resource"]
    if oauth.get("scope"):
        params["scope"] = oauth["scope"]
    if oauth.get("preset"):
        from .mcp_presets import PRESETS

        params.update(PRESETS[oauth["preset"]].extra_auth_params)
    return f"{oauth['authorization_endpoint']}?{urlencode(params)}"


def _token(oauth: dict, form: dict, *, timeout: float = 20.0) -> OAuthTokens:
    from ..security import decrypt

    form = {**form, "client_id": oauth["client_id"]}
    if oauth.get("resource"):
        form["resource"] = oauth["resource"]
    if oauth.get("preset"):
        from .mcp_presets import PRESETS

        _id, secret = PRESETS[oauth["preset"]].client()
        if secret:
            form["client_secret"] = secret
    elif oauth.get("client_secret"):
        form["client_secret"] = decrypt(oauth["client_secret"])
    try:
        response = httpx.post(oauth["token_endpoint"], data=form, timeout=timeout,
                              headers={"Accept": "application/json"})
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise OAuthError("The tool's sign-in did not answer.", cause=exc) from exc
    if response.status_code in (400, 401) and form.get("grant_type") == "refresh_token":
        raise OAuthReauthRequiredError("Sign in to this tool again.")
    if response.status_code >= 400 or not data.get("access_token"):
        raise OAuthError(f"The tool's sign-in refused: {data.get('error') or response.status_code}")
    return OAuthTokens(access_token=data["access_token"], refresh_token=data.get("refresh_token"),
                       expires_at=compute_expires_at(data.get("expires_in")),
                       external_workspace_id="", external_workspace_name=None)


def exchange(oauth: dict, *, code: str, verifier: str, redirect_uri: str) -> OAuthTokens:
    return _token(oauth, {"grant_type": "authorization_code", "code": code,
                          "code_verifier": verifier, "redirect_uri": redirect_uri})


class McpOAuth:
    """Refresh for ``user_connections.fresh_user_token``: one server's sign-in."""

    def __init__(self, oauth: dict) -> None:
        self.oauth = oauth

    def refresh(self, refresh_token: str) -> OAuthTokens:
        return _token(self.oauth, {"grant_type": "refresh_token", "refresh_token": refresh_token})
