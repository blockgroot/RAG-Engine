"""Auth router: signup-approval queue, admin-invited member login, admin
OAuth "Connect" flow (Phase 13, simplified — domain auto-join removed;
self-serve org creation gated behind manual approval, not a whitelist).

Three entry points, all converging on the same passwordless magic-link
session issuance — there is exactly one way to log in, admin or employee:
1. Signup (``/auth/signup``) — a brand-new company's first user. Does NOT
   create an org or account synchronously: it queues a pending
   ``org_signup_requests`` row and emails the platform owner
   (``EmailSettings.owner_notification_email``) a notification with
   one-click approve/reject confirmation links — the ONLY review surface,
   deliberately no CLI/admin UI. Only once approved does the org + admin
   user get created and a magic-link sign-in email go out to the requester.
2. Magic link (``/auth/magic-link`` + ``/auth/magic-link/verify``) — the
   normal way back in for anyone who ALREADY has an account (an admin whose
   signup request was approved, or a member an admin invited via
   ``/admin/members``). An email with no existing account has no path to a
   first login here — only an admin invite or an approved signup request
   creates one.

   **This endpoint DOES tell the caller whether an account exists** (``status``
   is ``sent`` or ``no_account``). That is a deliberate reversal of the original
   design, which returned one identical message either way so the endpoint could
   not be used to enumerate accounts. It was changed on request because the
   uniform response stranded real users: someone whose company has not onboarded
   got "check your inbox" and waited for an email that was never coming, with no
   way to find out why. The accepted cost is that this endpoint is now an
   enumeration oracle. It is mitigated, not eliminated, by per-IP rate limiting
   (``check_rate_limit``) so existence can be checked but not harvested in bulk.
   To restore the original guarantee, collapse the two returns back into one
   message and drop ``status`` from the response.
3. OAuth connect (``/auth/{provider}/authorize`` + ``/auth/{provider}/callback``)
   — admin-only, requires an existing session, used to link an org's Notion
   (etc.) workspace. Fully separate from magic-link auth; it never issues a
   session itself, only an ``oauth_connections`` row.

The signup-approval one-click links (``/auth/signup-requests/approve`` and
``/auth/signup-requests/reject``) are deliberately GET-a-confirmation-page,
POST-to-act: a GET alone never mutates state, so a mail client or security
scanner prefetching the link in the notification email can't silently
approve/reject a request. Only clicking the button on the confirmation page
(a same-URL POST) does. Like magic-link tokens, these are bearer possession
tokens (single-use, expiring, hash-stored) — not a new authenticated
HTTP/session surface.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..auth import (
    GitHubAppProvider,
    build_oauth_provider,
    consume_approve_token,
    consume_magic_link_token,
    consume_reject_token,
    consume_state,
    create_magic_link_token,
    create_session_token,
    create_state,
    create_signup_request,
    get_pending_request_for_email,
    get_request_by_approve_token,
    get_request_by_reject_token,
    get_user_by_email,
    save_connection,
    set_connection_config,
    send_magic_link_email_safe,
    send_signup_approved_email_safe,
    send_signup_rejected_email_safe,
    send_signup_request_notification_email_safe,
)
from ..config.settings import ApiSettings, AuthSettings, EmailSettings, RateLimitSettings
from ..core.exceptions import AuthError, ConfigurationError, OAuthError, SourceError
from ..githublive import refresh_installation_scope
from ..security.rate_limit import check_rate_limit
from ..vectorstore import build_vector_store
from ..workspaces import assert_member
from .deps import SESSION_COOKIE_FLAGS, SESSION_COOKIE_NAME, get_session

router = APIRouter(prefix="/auth", tags=["auth"])


def _dev_link(link: str) -> str | None:
    """Echo the magic link back in the response when in ``console`` email mode.

    Only ever set when no real email is actually going out (local dev / a
    self-hosted deployment that hasn't configured SMTP yet) — in that mode
    there is no inbox to check, so hiding the link would just mean digging it
    out of server logs. The moment ``EMAIL_SENDER=smtp`` is configured, this
    is always ``None`` and the link only ever exists in the sent email.
    """
    return link if EmailSettings.from_env().sender == "console" else None


def _build_magic_link(email: str) -> str:
    token = create_magic_link_token(email)
    base = (ApiSettings.from_env().frontend_url or "").rstrip("/")
    return f"{base}/verify?token={token}"


@router.post("/signup")
def signup(body: dict, background_tasks: BackgroundTasks, http_request: Request):
    """Queue a request to create a brand-new org — does NOT create it.

    No org or account is created here. The request lands in
    ``org_signup_requests`` as ``pending`` until the platform owner reviews
    it via the one-click email links; only on approval does the org + its
    admin user get created and a sign-in link get emailed. An email that's
    already a user anywhere, or already has a pending request, is rejected.
    """
    email = (body.get("email") or "").strip().lower()
    company_name = (body.get("company_name") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not company_name:
        raise HTTPException(status_code=400, detail="A company name is required")
    if get_user_by_email(email) is not None:
        raise HTTPException(status_code=400, detail="An account already exists for this email")
    if get_pending_request_for_email(email) is not None:
        raise HTTPException(
            status_code=400, detail="A request for this email is already pending review"
        )

    try:
        request = create_signup_request(email, company_name)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    owner_email = EmailSettings.from_env().owner_notification_email
    if owner_email:
        base = str(http_request.base_url).rstrip("/")
        approve_link = f"{base}/auth/signup-requests/approve?token={request.approve_token}"
        reject_link = f"{base}/auth/signup-requests/reject?token={request.reject_token}"
        background_tasks.add_task(
            send_signup_request_notification_email_safe,
            owner_email,
            email,
            company_name,
            approve_link,
            reject_link,
        )

    return {
        "message": (
            "Thanks — your request to create an organization has been received "
            "and is pending review."
        )
    }


@router.post("/magic-link")
def request_magic_link(
    body: dict,
    background_tasks: BackgroundTasks,
    request: Request,
    settings: ApiSettings = Depends(ApiSettings.from_env),
):
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    # This endpoint now REPORTS whether an account exists (see the docstring
    # note below), which makes it an enumeration surface. Rate-limit it per
    # client so the answer cannot be harvested in bulk: knowing one address is
    # unregistered is a support answer, sweeping a whole domain is recon.
    # Keyed on the caller's IP because there is no session here by definition.
    client_ip = (request.client.host if request.client else "unknown")
    check_rate_limit(
        f"magic-link:{client_ip}",
        limit=RateLimitSettings.from_env().auth_requests_per_window,
    )

    # Only an EXISTING account (created at signup, or by an admin invite via
    # /admin/members) ever gets a link — there is no auto-join path left that
    # creates a first account here.
    user = get_user_by_email(email)
    dev_link = None
    known = user is not None and user.org_id is not None
    if known:
        token = create_magic_link_token(email)
        base = (settings.frontend_url or "").rstrip("/")
        link = f"{base}/verify?token={token}"
        background_tasks.add_task(send_magic_link_email_safe, email, link)
        dev_link = _dev_link(link)

    if known:
        return {
            "status": "sent",
            "message": "A sign-in link is on its way. It expires shortly and works once.",
            "dev_link": dev_link,
        }

    # NOTE ON WORDING: we say "no account", NOT "your organisation is not
    # registered", because the backend cannot tell those apart. `org_domains`
    # was removed, so there is no domain->org mapping: an unknown email may
    # belong to a company that IS set up and simply has not invited this person
    # yet. Claiming the organisation does not exist would be wrong in exactly
    # the common case (a new hire at an existing customer) and would send them
    # to sign their company up a second time.
    return {
        "status": "no_account",
        "message": (
            "We couldn't find an account for that email. If your company already "
            "uses Handbook, ask an admin to invite you. If not, you can set your "
            "company up."
        ),
        "dev_link": None,
    }


@router.get("/magic-link/verify")
def verify_magic_link(token: str, settings: ApiSettings = Depends(ApiSettings.from_env)):
    try:
        email = consume_magic_link_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = get_user_by_email(email)
    if user is None or user.org_id is None:
        # Shouldn't happen on the normal path (the user is created with org_id
        # already resolved in request_magic_link), but never issue a session
        # for an org-less user under any circumstance.
        raise HTTPException(status_code=401, detail="No resolved organization for this account")

    try:
        session_token = create_session_token(user)
    except (AuthError, ConfigurationError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Redirect to the FRONTEND (not this API's own host) — this endpoint is
    # reached by a real browser navigation from the emailed link, so the
    # Set-Cookie below is what actually lands the session; the redirect target
    # is purely where the user ends up next.
    # Land on `/` so the frontend can route by role + setup status
    # (admin → onboarding if incomplete, else chat; member → chat/waiting).
    base = (settings.frontend_url or "").rstrip("/")
    response = RedirectResponse(url=f"{base}/" if base else "/")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        # Without max_age this is a browser-session cookie (cleared on
        # browser close) regardless of how long the JWT itself is valid for
        # — that would silently defeat the point of a long-lived session TTL.
        # Keep it tied to the same setting so the cookie's lifetime always
        # matches the token's. Path=/ so a Set-Cookie on /api/auth/.../verify
        # is still sent to /api/me after the Next.js rewrite.
        max_age=AuthSettings.from_env().session_ttl_minutes * 60,
        **SESSION_COOKIE_FLAGS,
    )
    return response


@router.post("/logout")
def logout():
    """Clear the session cookie. Idempotent — safe even when already signed out."""
    response = JSONResponse({"status": "signed_out"})
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        **SESSION_COOKIE_FLAGS,
    )
    return response


@router.get("/{provider}/authorize")
def authorize(provider: str, workspace_id: str | None = None, session=Depends(get_session)):
    """Start a connect flow.

    Without ``workspace_id``: the existing org-wide admin connect flow —
    admin role required, unchanged. With ``workspace_id`` (Workspace-within-
    a-Workspace): an employee connecting their OWN personal source into a
    sub-workspace they belong to. Restricted to the workspace's ``owner``
    (its creator) — see CLAUDE.md's Workspace-within-a-Workspace plan §Task 12
    decision 3 — so an ordinary member can't silently repoint the workspace's
    data source.
    """
    if workspace_id is None:
        if session.role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required")
    else:
        # GitHub was org-level-only when first built (a per-workspace repo subset
        # introduces repo-level access control inside an org, which nothing else
        # here has). That restriction was lifted on request: a workspace owner may
        # connect their own installation, which makes workspace membership a real
        # access boundary over code as well as documents.
        #
        # What keeps that safe is NOT a check here but the scoping downstream:
        # every GitHub read resolves its token and repo allowlist from
        # ``(org_id, workspace_id)`` together, so a workspace with no GitHub
        # connection raises rather than falling back to the org-wide one. See
        # tests/test_github_workspace_scope.py — that no-fallback property is the
        # whole reason this is safe, so do not "helpfully" add a fallback.
        try:
            role = assert_member(workspace_id, session.org_id, session.user_id)
        except AuthError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if role != "owner":
            raise HTTPException(
                status_code=403, detail="Only the workspace owner can connect a source"
            )

    try:
        oauth_provider = build_oauth_provider(provider)
    except ConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state = create_state(session.org_id, provider, workspace_id=workspace_id)
    return RedirectResponse(url=oauth_provider.authorize_url(state))


def _frontend_redirect(settings: ApiSettings, path: str) -> RedirectResponse:
    base = (settings.frontend_url or "").rstrip("/")
    return RedirectResponse(url=f"{base}{path}" if base else path)


def _github_conflict_error_code(conflict_workspace_id: str | None) -> str:
    """Map a conflicting surface to a frontend banner code."""
    # Org-wide conflict → the familiar "same as company" copy on spaces.
    if conflict_workspace_id is None:
        return "github_same_install"
    return "github_install_in_use"


def _github_connect_error_path(
    workspace_id: str | None, code: str
) -> str:
    if workspace_id is not None:
        return f"/workspaces/{workspace_id}?connect_error={code}"
    return f"/admin/connections?connect_error={code}"


def _github_connect_success_path(workspace_id: str | None) -> str:
    if workspace_id is not None:
        return f"/workspaces/{workspace_id}?connected=github"
    return "/onboarding?connected=github"


def _persist_github_connection(
    org_id: str,
    workspace_id: str | None,
    tokens,
    installation_id: str,
) -> str | None:
    """Save the GitHub connection or return a connect_error code.

    Enforces **org-wide uniqueness of ``installation_id``**: Company Sources and
    every space must bind different GitHub App installs. Reconnecting the same
    surface with the same install is allowed.
    """
    from ..auth.github_installations import find_github_installation_conflict

    conflict = find_github_installation_conflict(
        org_id, installation_id, for_workspace_id=workspace_id
    )
    if conflict is not None:
        return _github_conflict_error_code(conflict.workspace_id)

    save_connection(org_id, "github", tokens, workspace_id=workspace_id)
    set_connection_config(
        org_id,
        "github",
        {
            "installation_id": installation_id,
            "account_login": tokens.external_workspace_id,
        },
        workspace_id=workspace_id,
    )
    try:
        refresh_installation_scope(org_id, workspace_id)
    except (OAuthError, ConfigurationError, SourceError):
        pass
    return None


@router.get("/github/installations/pending/{token}")
def github_install_pending_detail(token: str):
    """List GitHub App installs the user may bind, with availability flags.

    Possession of ``token`` is the capability (same model as oauth ``state``).
    Installs already used by another Folio surface in this org are marked
    unavailable so Company Sources and a space cannot share one personal id.
    """
    from ..auth.github_installations import (
        find_github_installation_conflict,
        summarize_installation,
    )
    from ..auth.github_pending import get_github_install_pending

    try:
        pending = get_github_install_pending(token)
        provider = build_oauth_provider("github")
        assert isinstance(provider, GitHubAppProvider)
        raw = provider._list_installations(pending.access_token)
    except (OAuthError, ConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    choices = []
    for item in raw:
        summary = summarize_installation(item)
        conflict = find_github_installation_conflict(
            pending.org_id,
            summary["id"],
            for_workspace_id=pending.workspace_id,
        )
        available = conflict is None and bool(summary["login"])
        reason = None
        if conflict is not None:
            reason = (
                "Already linked under Company → Sources"
                if conflict.workspace_id is None
                else "Already linked to another space in this company"
            )
        choices.append({**summary, "available": available, "unavailable_reason": reason})

    # Fresh state so "Connect another account" can open GitHub's install
    # screen and still return to our callback for the same Folio surface.
    fresh_state = create_state(
        pending.org_id, "github", workspace_id=pending.workspace_id
    )
    install_another_url = provider.install_url(fresh_state)
    # Logout first so GitHub shows the account picker; return_to is a path on
    # github.com (install URL without the host).
    from urllib.parse import urlparse, urlencode

    install_path = urlparse(install_another_url).path
    install_query = urlparse(install_another_url).query
    return_to = install_path + (f"?{install_query}" if install_query else "")
    switch_account_url = (
        "https://github.com/logout?" + urlencode({"return_to": return_to})
    )

    return {
        "scope": "workspace" if pending.workspace_id else "org",
        "workspace_id": pending.workspace_id,
        "installations": choices,
        "hint": (
            "Pick the GitHub account this *space* should use. It must be different "
            "from Company → Sources."
            if pending.workspace_id
            else "Pick the GitHub account for *Company → Sources* (usually your "
            "company Organization). Spaces will need a different account."
        ),
        "install_another_url": install_another_url,
        "switch_account_url": switch_account_url,
    }


@router.post("/github/installations/pending/{token}")
def github_install_pending_choose(
    token: str,
    body: dict,
    settings: ApiSettings = Depends(ApiSettings.from_env),
):
    """Complete connect after the user picks an installation on the choose page."""
    from ..auth.github_pending import consume_github_install_pending

    installation_id = str((body or {}).get("installation_id") or "").strip()
    if not installation_id:
        raise HTTPException(status_code=400, detail="installation_id is required")

    try:
        pending = consume_github_install_pending(token)
        provider = build_oauth_provider("github")
        assert isinstance(provider, GitHubAppProvider)
        tokens, verified_id = provider.tokens_for_installation(
            pending.access_token,
            installation_id,
            refresh_token=pending.refresh_token,
            expires_at=pending.token_expires_at,
        )
        error = _persist_github_connection(
            pending.org_id, pending.workspace_id, tokens, verified_id
        )
    except (OAuthError, ConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if error:
        path = _github_connect_error_path(pending.workspace_id, error)
    else:
        path = _github_connect_success_path(pending.workspace_id)
    # Return the absolute URL so the SPA can window.location without guessing
    # FRONTEND_URL itself (API already knows it).
    base = (settings.frontend_url or "").rstrip("/")
    return {"redirect_to": f"{base}{path}" if base else path}


@router.get("/{provider}/callback")
def callback(
    provider: str,
    code: str,
    state: str,
    installation_id: str | None = None,
    setup_action: str | None = None,
    settings: ApiSettings = Depends(ApiSettings.from_env),
):
    """Finish a connect flow.

    ``installation_id``/``setup_action`` are GitHub-only extras (a GitHub App
    install redirect carries them; Notion and Google do not). Both are treated
    as **untrusted** — ``installation_id`` is verified against the authorizing
    user's own installations inside
    ``GitHubAppProvider.exchange_code_with_installation`` before anything is
    persisted, because GitHub's docs warn it can be spoofed (see
    ``app/auth/github_oauth.py``). ``setup_action`` is accepted only so the
    redirect doesn't 422 on an unexpected query parameter; nothing branches on
    it.

    GitHub without ``installation_id`` no longer auto-picks an install: it
    parks the user token and sends the browser to the choose UI so Company
    Sources vs a space never silently share one personal GitHub account.
    """
    from ..auth.github_pending import create_github_install_pending

    try:
        org_id, workspace_id = consume_state(state, provider=provider)
        oauth_provider = build_oauth_provider(provider)

        if isinstance(oauth_provider, GitHubAppProvider):
            if installation_id:
                # Came from GitHub's install screen — account was chosen there.
                tokens, verified_installation_id = (
                    oauth_provider.exchange_code_with_installation(
                        code, installation_id
                    )
                )
                error = _persist_github_connection(
                    org_id, workspace_id, tokens, verified_installation_id
                )
                if error:
                    return _frontend_redirect(
                        settings, _github_connect_error_path(workspace_id, error)
                    )
            else:
                access, refresh, expires_at, installations = (
                    oauth_provider.exchange_code_list_installations(code)
                )
                if not installations:
                    fresh = create_state(org_id, provider, workspace_id=workspace_id)
                    return RedirectResponse(url=oauth_provider.install_url(fresh))
                pending = create_github_install_pending(
                    org_id,
                    workspace_id=workspace_id,
                    access_token=access,
                    refresh_token=refresh,
                    token_expires_at=expires_at,
                )
                return _frontend_redirect(
                    settings, f"/connect/github/choose?pending={pending}"
                )
        else:
            tokens = oauth_provider.exchange_code(code)
            save_connection(org_id, provider, tokens, workspace_id=workspace_id)
    except (OAuthError, ConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _frontend_redirect(
        settings,
        (
            f"/workspaces/{workspace_id}?connected={provider}"
            if workspace_id is not None
            else f"/onboarding?connected={provider}"
        ),
    )


# A trimmed, self-contained copy of the subset of frontend/app/globals.css
# ("Harbor Desk" — teal ink on mist panels) this page actually uses. This
# page is served by the API, not the Next.js app, so it can't import that
# stylesheet or next/font's self-hosted Outfit — the font falls back to the
# same system stack globals.css itself falls back to (--font-ui-fallback),
# so the page still reads as the same product even without the webfont.
_PAGE_STYLE = """
:root {
  --font-ui: "Avenir Next", "Segoe UI", sans-serif;
  --bg: #e8eef0; --surface: #f7faf9; --surface-elevated: #ffffff;
  --ink: #12201e; --ink-muted: #5a6d69;
  --border: #d3dedb; --border-strong: #b4c4bf;
  --accent: #0f7a74; --accent-strong: #0b5f5a; --accent-soft: #d4efec; --accent-ink: #f4fffd;
  --accent-glow: rgba(15, 122, 116, 0.22);
  --ok: #1a7a52; --warn: #9a5b18;
  --radius: 16px; --radius-sm: 11px;
  --shadow-soft: 0 12px 28px -20px rgba(18, 32, 30, 0.45);
  --ease: cubic-bezier(0.22, 1, 0.36, 1);
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; color: var(--ink); font-family: var(--font-ui); }
body {
  min-height: 100vh; display: grid; place-items: center; padding: 3rem 1.25rem;
  background:
    radial-gradient(900px 520px at 12% -8%, rgba(15, 122, 116, 0.14), transparent 55%),
    linear-gradient(165deg, #eef3f2 0%, #e4ebea 55%, #dde7e5 100%);
  background-attachment: fixed;
}
.panel { width: min(100%, 440px); animation: rise-in 0.45s var(--ease) both; }
@keyframes rise-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.brand-lockup { display: flex; align-items: center; gap: 0.55rem; margin-bottom: 1.5rem; }
.brand-mark {
  width: 1.9rem; height: 1.9rem; flex: none; display: block; object-fit: contain;
}
.brand-name { font-weight: 650; font-size: 1.2rem; letter-spacing: -0.02em; }
.eyebrow {
  margin: 0 0 0.4rem; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.11em;
  text-transform: uppercase; color: var(--accent-strong);
}
h1 { font-size: 1.6rem; font-weight: 650; letter-spacing: -0.03em; margin: 0 0 0.35rem; line-height: 1.15; }
.muted { color: var(--ink-muted); font-size: 0.92rem; line-height: 1.5; margin: 0; }
.card {
  background: color-mix(in srgb, var(--surface) 94%, white);
  border: 1px solid color-mix(in srgb, var(--border) 85%, white);
  border-radius: var(--radius); padding: 1.25rem 1.35rem; box-shadow: var(--shadow-soft);
  margin-top: 1.25rem;
}
.stack > * + * { margin-top: 1.1rem; }
.field { display: flex; flex-direction: column; gap: 0.4rem; }
.field label { font-size: 0.8rem; font-weight: 650; color: var(--ink-muted); }
.input {
  font: inherit; font-size: 1rem; padding: 0.75rem 0.9rem; border: 1px solid var(--border);
  border-radius: var(--radius-sm); background: var(--surface-elevated); color: var(--ink);
  width: 100%;
}
.input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-glow); }
.button {
  font: inherit; font-size: 0.95rem; font-weight: 650; padding: 0.75rem 1.2rem;
  border-radius: var(--radius-sm); border: 1px solid transparent; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; gap: 0.45rem; width: 100%;
  background: linear-gradient(145deg, #14938c 0%, var(--accent-strong) 100%); color: var(--accent-ink);
  box-shadow: 0 10px 20px -14px rgba(15, 122, 116, 0.85);
}
.button-danger { background: linear-gradient(145deg, #c4622f 0%, #9a4620 100%); box-shadow: 0 10px 20px -14px rgba(154, 70, 32, 0.6); }
.summary-row { display: flex; justify-content: space-between; gap: 1rem; font-size: 0.92rem; padding: 0.5rem 0; }
.summary-row + .summary-row { border-top: 1px solid var(--border); }
.summary-label { color: var(--ink-muted); }
.summary-value { font-weight: 650; text-align: right; }
.banner {
  padding: 0.95rem 1.1rem; border-radius: var(--radius-sm); border: 1px solid var(--border);
  background: var(--surface-elevated); font-size: 0.94rem; box-shadow: var(--shadow-soft);
}
.banner-ok {
  border-color: color-mix(in srgb, var(--ok) 40%, var(--border));
  background: linear-gradient(135deg, color-mix(in srgb, var(--ok) 12%, var(--surface)), var(--surface));
}
.banner-warn {
  border-color: color-mix(in srgb, var(--warn) 45%, var(--border));
  background: color-mix(in srgb, var(--warn) 10%, var(--surface));
  color: var(--warn);
}
"""


def _brand_logo_url() -> str:
    """The real Handbook mark (``frontend/public/brand/handbook-mark.png``),
    served by the FRONTEND, not this API — this page can't `import` that
    static asset (see the module-level design note above `_PAGE_STYLE`), so
    it's referenced by absolute URL instead. Uses the small 192px export
    since this is an icon-sized placement, not a hero image. Falls back to
    no image (rather than a broken-image icon) if ``FRONTEND_URL`` is unset,
    e.g. a bare local API run with no frontend configured."""
    base = (ApiSettings.from_env().frontend_url or "").rstrip("/")
    return f"{base}/brand/handbook-mark-192.png" if base else ""


def _page(title: str, body: str) -> str:
    logo_url = _brand_logo_url()
    logo_html = (
        f'<img class="brand-mark" src="{logo_url}" alt="" aria-hidden="true" '
        f'onerror="this.style.display=\'none\'">'
        if logo_url
        else ""
    )
    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)} · Handbook</title>
  <style>{_PAGE_STYLE}</style>
</head>
<body>
  <div class="panel">
    <div class="brand-lockup">
      {logo_html}
      <span class="brand-name">Handbook</span>
    </div>
    {body}
  </div>
</body>
</html>
""".strip()


def _confirm_page(action: str, email: str, company_name: str, token: str) -> str:
    """Confirmation page matching the app's own design (see `_PAGE_STYLE`).
    GET renders this and does NOT mutate anything (see module docstring for
    why); only the button's POST back to the same URL does."""
    verb = "Approve" if action == "approve" else "Reject"
    button_class = "button" if action == "approve" else "button button-danger"
    extra_field = (
        f"""<div class="field">
      <label for="reason">Reason (optional, shown to the requester)</label>
      <input class="input" id="reason" name="reason" placeholder="e.g. duplicate of an existing tenant">
    </div>"""
        if action == "reject"
        else ""
    )
    body = f"""
    <p class="eyebrow">Organization request</p>
    <h1>{verb} this request?</h1>
    <p class="muted">Review the details below before you decide.</p>
    <div class="card stack">
      <div>
        <div class="summary-row">
          <span class="summary-label">Email</span>
          <span class="summary-value">{html.escape(email)}</span>
        </div>
        <div class="summary-row">
          <span class="summary-label">Company</span>
          <span class="summary-value">{html.escape(company_name)}</span>
        </div>
      </div>
      <form method="post" class="stack">
        <input type="hidden" name="token" value="{html.escape(token)}">
        {extra_field}
        <button class="{button_class}" type="submit">{verb} request</button>
      </form>
    </div>
    """
    return _page(f"{verb} organization request", body)


def _result_page(message: str, *, ok: bool = True) -> str:
    banner_class = "banner banner-ok" if ok else "banner banner-warn"
    body = f"""
    <p class="eyebrow">Organization request</p>
    <h1>{"Done" if ok else "Heads up"}</h1>
    <div class="{banner_class}">{html.escape(message)}</div>
    """
    return _page("Signup request", body)


@router.get("/signup-requests/approve", response_class=HTMLResponse)
def confirm_approve_signup_request(token: str):
    """GET only renders a confirmation page — never approves. See module
    docstring: this is what stops an email scanner prefetching the link
    from silently creating an org."""
    request = get_request_by_approve_token(token)
    if request is None:
        return HTMLResponse(
            _result_page("This link is invalid, expired, or already used.", ok=False)
        )
    return HTMLResponse(_confirm_page("approve", request.email, request.company_name, token))


@router.post("/signup-requests/approve", response_class=HTMLResponse)
def do_approve_signup_request(token: str = Form(...)):
    store = build_vector_store()
    try:
        request, org_id = consume_approve_token(token, store=store)
    except AuthError as exc:
        return HTMLResponse(_result_page(str(exc), ok=False))

    link = _build_magic_link(request.email)
    send_signup_approved_email_safe(request.email, link)
    return HTMLResponse(
        _result_page(f"Approved. {request.company_name} ({request.email}) can now sign in.")
    )


@router.get("/signup-requests/reject", response_class=HTMLResponse)
def confirm_reject_signup_request(token: str):
    request = get_request_by_reject_token(token)
    if request is None:
        return HTMLResponse(
            _result_page("This link is invalid, expired, or already used.", ok=False)
        )
    return HTMLResponse(_confirm_page("reject", request.email, request.company_name, token))


@router.post("/signup-requests/reject", response_class=HTMLResponse)
def do_reject_signup_request(token: str = Form(...), reason: str = Form("")):
    try:
        request = consume_reject_token(token, reason=reason or None)
    except AuthError as exc:
        return HTMLResponse(_result_page(str(exc), ok=False))

    send_signup_rejected_email_safe(request.email, reason or None)
    return HTMLResponse(_result_page(f"Rejected the request from {request.email}."))
