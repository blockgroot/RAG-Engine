"""Sign the bench in to Linear's MCP server, and keep it signed in.

    .venv/bin/python -m evaluation.linear_signin

Opens Linear's sign-in in the browser. Sign in with the account whose workspace
has the BEN team and approve. The token, its refresh token and this client's
registration go into ``.env.bench`` (git-ignored); nothing is printed. With the
registration saved, ``refresh()`` renews an expired token without a browser,
which the scenario suite does by itself.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import re
import secrets
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

MCP = "https://mcp.linear.app/mcp"
ENV = Path(__file__).resolve().parent.parent / ".env.bench"
PORT = 8765
REDIRECT = f"http://127.0.0.1:{PORT}/callback"
#: The browser sign-in, at most.
WAIT_SECONDS = 300


def _endpoints() -> dict:
    """Linear's authorization server, as its MCP server advertises it."""
    resource = httpx.get("https://mcp.linear.app/.well-known/oauth-protected-resource", timeout=20).json()
    issuer = resource["authorization_servers"][0].rstrip("/")
    return httpx.get(f"{issuer}/.well-known/oauth-authorization-server", timeout=20).json()


def _save(values: dict[str, str]) -> None:
    """Set each key in ``.env.bench``: every existing line of it replaced, or
    one appended. The rest of the file is left as it is."""
    text = ENV.read_text() if ENV.exists() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        text, n = re.subn(rf"^{re.escape(key)}=.*$", lambda _m: line, text, flags=re.MULTILINE)
        if not n:
            text = text.rstrip("\n") + f"\n{line}\n"
    ENV.write_text(text)


def _tokens(client: dict, form: dict) -> dict:
    form = {**form, "client_id": client["client_id"]}
    if client.get("client_secret"):
        form["client_secret"] = client["client_secret"]
    response = httpx.post(client["token_endpoint"], data=form, timeout=20,
                          headers={"Accept": "application/json"})
    data = response.json()
    if response.status_code >= 400 or not data.get("access_token"):
        raise SystemExit(f"Linear refused the sign-in: {data.get('error') or response.status_code}")
    return data


def _store(client: dict, data: dict) -> None:
    _save({"BENCH_LINEAR_MCP_TOKEN": data["access_token"],
           "BENCH_LINEAR_MCP_REFRESH": data.get("refresh_token") or "",
           "BENCH_LINEAR_MCP_CLIENT": json.dumps(client, separators=(",", ":"))})


def refresh() -> str | None:
    """A new access token from the saved refresh token, saved too; ``None``
    when there is nothing to refresh with (run the sign-in again)."""
    values = {}
    for line in ENV.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep:
            values[key.strip()] = value.strip()
    if not values.get("BENCH_LINEAR_MCP_CLIENT") or not values.get("BENCH_LINEAR_MCP_REFRESH"):
        return None
    client = json.loads(values["BENCH_LINEAR_MCP_CLIENT"])
    try:
        data = _tokens(client, {"grant_type": "refresh_token", "refresh_token": values["BENCH_LINEAR_MCP_REFRESH"]})
    except SystemExit:
        return None
    _store(client, data)
    return data["access_token"]


def main() -> int:
    meta = _endpoints()
    registered = httpx.post(meta["registration_endpoint"], timeout=20, json={
        "client_name": "Handbook bench", "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post"}).json()
    client = {"token_endpoint": meta["token_endpoint"], "client_id": registered["client_id"],
              "client_secret": registered.get("client_secret"), "resource": MCP}

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    got: dict = {}

    class Callback(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the http.server name
            query = parse_qs(urlparse(self.path).query)
            if urlparse(self.path).path == "/callback":
                got.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Signed in. You can close this tab.")

        def log_message(self, *_):
            pass

    server = http.server.HTTPServer(("127.0.0.1", PORT), Callback)
    threading.Thread(target=server.handle_request, daemon=True).start()
    url = meta["authorization_endpoint"] + "?" + urlencode({
        "response_type": "code", "client_id": client["client_id"], "redirect_uri": REDIRECT,
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "resource": MCP})
    print("Opening Linear's sign-in. Sign in with the account that has the BEN team, then approve.")
    webbrowser.open(url)
    for _ in range(WAIT_SECONDS):
        if got:
            break
        threading.Event().wait(1)
    server.server_close()
    if got.get("state") != state or not got.get("code"):
        print(f"No sign-in came back ({got.get('error') or 'timed out'}). Nothing was saved.")
        return 1
    data = _tokens(client, {"grant_type": "authorization_code", "code": got["code"],
                            "code_verifier": verifier, "redirect_uri": REDIRECT, "resource": MCP})
    _store(client, data)
    print("Saved to .env.bench. The bench is signed in to Linear.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
