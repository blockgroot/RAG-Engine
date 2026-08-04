"""Auth router: whitelist-gated signup, admin-invited member login, admin
OAuth "Connect" flow (Phase 13, simplified — domain auto-join removed).

Three entry points, all converging on the same passwordless magic-link
session issuance — there is exactly one way to log in, admin or employee:
1. Signup (``/auth/signup``) — creates a brand-new org and makes this user
   its admin, but ONLY for an email on the DB-backed
   ``owner_email_whitelist`` table (``app/auth/owner_whitelist.py``, managed
   via ``scripts/manage_owner_whitelist.py``); anyone else gets a 403. This
   is deliberately narrow: it gates the moment a new org is born, nothing
   else. Everyone who isn't pre-approved to create an org instead joins an
   EXISTING one via an admin invite (``/admin/members``) — see that router.
   A whitelisted signup emails the new admin a magic link. No password to
   set, no separate "admin login" flow.
2. Magic link (``/auth/magic-link`` + ``/auth/magic-link/verify``) — the
   normal way back in for anyone who ALREADY has an account (an admin, or a
   member an admin invited via ``/admin/members``). An email with no existing
   account has no path to a first login here — only an admin invite (or
   signup, for a brand-new org) creates one. There is deliberately no
   response-content difference between "no account" and "email sent" so this
   endpoint can't be used to enumerate registered accounts.
3. OAuth connect (``/auth/{provider}/authorize`` + ``/auth/{provider}/callback``)
   — admin-only, requires an existing session, used to link an org's Notion
   (etc.) workspace. Fully separate from magic-link auth; it never issues a
   session itself, only an ``oauth_connections`` row.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..auth import (
    build_oauth_provider,
    consume_magic_link_token,
    consume_state,
    create_admin,
    create_magic_link_token,
    create_session_token,
    create_state,
    get_user_by_email,
    is_whitelisted,
    save_connection,
    send_magic_link_email_safe,
)
from ..config.settings import ApiSettings, AuthSettings, EmailSettings
from ..core.exceptions import AuthError, ConfigurationError, OAuthError
from ..vectorstore.base import VectorStore
from ..workspaces import assert_member
from .deps import SESSION_COOKIE_NAME, get_session, get_vector_store

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


@router.post("/signup")
def signup(
    body: dict,
    background_tasks: BackgroundTasks,
    settings: ApiSettings = Depends(ApiSettings.from_env),
    store: VectorStore = Depends(get_vector_store),
):
    """Create a brand-new org + its first admin user, then email a login link.

    Gated: only an email on the DB-backed ``owner_email_whitelist`` table
    may create a new org this way — everyone else gets a 403. An email
    that's already a user anywhere is rejected rather than silently creating
    a second, disconnected account for it.
    """
    email = (body.get("email") or "").strip().lower()
    company_name = (body.get("company_name") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not company_name:
        raise HTTPException(status_code=400, detail="A company name is required")
    if not is_whitelisted(email):
        raise HTTPException(
            status_code=403, detail="This email is not authorized to create an organization"
        )
    if get_user_by_email(email) is not None:
        raise HTTPException(status_code=400, detail="An account already exists for this email")

    org_id = store.create_organization(company_name)
    create_admin(email, org_id)

    token = create_magic_link_token(email)
    base = (settings.frontend_url or "").rstrip("/")
    link = f"{base}/verify?token={token}"
    # Gmail SMTP is multi-second; don't block the signup response on it.
    background_tasks.add_task(send_magic_link_email_safe, email, link)

    return {"message": "Check your email for a sign-in link.", "dev_link": _dev_link(link)}


@router.post("/magic-link")
def request_magic_link(
    body: dict,
    background_tasks: BackgroundTasks,
    settings: ApiSettings = Depends(ApiSettings.from_env),
):
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    # Only an EXISTING account (created at signup, or by an admin invite via
    # /admin/members) ever gets a link — there is no auto-join path left that
    # creates a first account here. An unknown email is a silent no-op.
    user = get_user_by_email(email)
    dev_link = None
    if user is not None and user.org_id is not None:
        token = create_magic_link_token(email)
        base = (settings.frontend_url or "").rstrip("/")
        link = f"{base}/verify?token={token}"
        background_tasks.add_task(send_magic_link_email_safe, email, link)
        dev_link = _dev_link(link)

    # The "message" text is always identical — never reveal whether the email
    # is known. "dev_link" DOES vary with whether an account exists, but it's
    # only ever non-None in console-email mode (no SMTP configured, i.e. local
    # dev with no real inbox and no real attacker) — in any deployment where
    # the anti-enumeration guarantee actually matters, EMAIL_SENDER=smtp and
    # this is always None, so the response is identical either way.
    return {"message": "If that email is eligible, a sign-in link has been sent.", "dev_link": dev_link}


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
        httponly=True,
        secure=True,
        samesite="lax",
        # Without max_age this is a browser-session cookie (cleared on
        # browser close) regardless of how long the JWT itself is valid for
        # — that would silently defeat the point of a long-lived session TTL.
        # Keep it tied to the same setting so the cookie's lifetime always
        # matches the token's.
        max_age=AuthSettings.from_env().session_ttl_minutes * 60,
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


@router.get("/{provider}/callback")
def callback(
    provider: str,
    code: str,
    state: str,
    settings: ApiSettings = Depends(ApiSettings.from_env),
):
    try:
        org_id, workspace_id = consume_state(state, provider=provider)
        oauth_provider = build_oauth_provider(provider)
        tokens = oauth_provider.exchange_code(code)
        save_connection(org_id, provider, tokens, workspace_id=workspace_id)
    except (OAuthError, ConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Resume admin onboarding at the ingest step after OAuth returns. A
    # workspace connect resumes in the workspace's own UI instead.
    base = (settings.frontend_url or "").rstrip("/")
    if workspace_id is not None:
        target = f"/workspaces/{workspace_id}?connected={provider}"
    else:
        target = f"/onboarding?connected={provider}"
    return RedirectResponse(url=f"{base}{target}" if base else target)
