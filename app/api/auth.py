"""Auth router: self-serve signup, employee magic-link login, admin OAuth
"Connect" flow (Phase 13, simplified post-Phase-14).

Three entry points, all converging on the same passwordless magic-link
session issuance — there is exactly one way to log in, admin or employee:
1. Signup (``/auth/signup``) — a brand-new company's first user. Creates the
   org, makes this user its admin, and emails them a magic link. No password
   to set, no separate "admin login" flow.
2. Magic link (``/auth/magic-link`` + ``/auth/magic-link/verify``) — an
   existing user's (admin or employee) normal path back in. For a new email
   at an already-registered, auto-join-enabled domain
   (``app.auth.domains.resolve_org_for_email``) this also transparently
   creates their member account. There is deliberately no response-content
   difference between "domain not eligible" and "email sent" so this endpoint
   can't be used to enumerate which companies are registered.
3. OAuth connect (``/auth/{provider}/authorize`` + ``/auth/{provider}/callback``)
   — admin-only, requires an existing session, used to link an org's Notion
   (etc.) workspace. Fully separate from magic-link auth; it never issues a
   session itself, only an ``oauth_connections`` row.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..auth import (
    build_oauth_provider,
    consume_magic_link_token,
    consume_state,
    create_admin,
    create_magic_link_token,
    create_session_token,
    create_state,
    get_or_create_member,
    get_user_by_email,
    resolve_org_for_email,
    save_connection,
    send_magic_link_email,
)
from ..config.settings import ApiSettings, EmailSettings
from ..core.exceptions import AuthError, ConfigurationError, OAuthError
from ..vectorstore.base import VectorStore
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
    settings: ApiSettings = Depends(ApiSettings.from_env),
    store: VectorStore = Depends(get_vector_store),
):
    """Create a brand-new org + its first admin user, then email a login link.

    Self-serve: no manual org-creation step on our side. An email that's
    already a user anywhere is rejected rather than silently creating a
    second, disconnected account for it.
    """
    email = (body.get("email") or "").strip().lower()
    company_name = (body.get("company_name") or "").strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")
    if not company_name:
        raise HTTPException(status_code=400, detail="A company name is required")
    if get_user_by_email(email) is not None:
        raise HTTPException(status_code=400, detail="An account already exists for this email")

    org_id = store.create_organization(company_name)
    create_admin(email, org_id)

    token = create_magic_link_token(email)
    base = (settings.frontend_url or "").rstrip("/")
    link = f"{base}/verify?token={token}"
    send_magic_link_email(email, link)

    return {"message": "Check your email for a sign-in link.", "dev_link": _dev_link(link)}


@router.post("/magic-link")
def request_magic_link(body: dict, settings: ApiSettings = Depends(ApiSettings.from_env)):
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    # An existing account can always request its own login link — auto-join
    # only gates a NEW email joining an org; it must never lock out someone
    # who already has an account there (e.g. an admin who registered the
    # domain and later turned auto-join off shouldn't lock themselves out).
    existing_user = get_user_by_email(email)
    org_id = existing_user.org_id if existing_user is not None else resolve_org_for_email(email)
    dev_link = None
    if org_id is not None:
        user = existing_user or get_or_create_member(email, org_id)
        token = create_magic_link_token(email)
        base = (settings.frontend_url or "").rstrip("/")
        link = f"{base}/verify?token={token}"
        send_magic_link_email(email, link)
        dev_link = _dev_link(link)
        _ = user  # created/reused; nothing further needed before the link is clicked

    # The "message" text is always identical — never reveal whether the domain
    # is registered. "dev_link" DOES vary with eligibility, but it's only ever
    # non-None in console-email mode (no SMTP configured, i.e. local dev with
    # no real inbox and no real attacker) — in any deployment where the
    # anti-enumeration guarantee actually matters, EMAIL_SENDER=smtp and this
    # is always None, so the response is identical either way.
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
    base = (settings.frontend_url or "").rstrip("/")
    response = RedirectResponse(url=f"{base}/chat" if base else "/chat")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        secure=True,
        samesite="lax",
    )
    return response


@router.get("/{provider}/authorize")
def authorize(provider: str, session=Depends(get_session)):
    if session.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    try:
        oauth_provider = build_oauth_provider(provider)
    except ConfigurationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state = create_state(session.org_id, provider)
    return RedirectResponse(url=oauth_provider.authorize_url(state))


@router.get("/{provider}/callback")
def callback(
    provider: str,
    code: str,
    state: str,
    settings: ApiSettings = Depends(ApiSettings.from_env),
):
    try:
        org_id = consume_state(state, provider=provider)
        oauth_provider = build_oauth_provider(provider)
        tokens = oauth_provider.exchange_code(code)
        save_connection(org_id, provider, tokens)
    except (OAuthError, ConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    base = (settings.frontend_url or "").rstrip("/")
    return RedirectResponse(url=f"{base}/admin/connections?connected={provider}")
