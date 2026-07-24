"""Auth router: employee magic-link login + admin OAuth "Connect" flow (Phase 13).

Two independent entry points into a session:
1. Magic link (``/auth/magic-link`` + ``/auth/magic-link/verify``) — an
   employee's normal path in. Only ever issues a session for an email whose
   org has already been resolved via a verified, auto-join-enabled domain
   (``app.auth.domains.resolve_org_for_email``); there is deliberately no
   response-content difference between "domain not eligible" and "email sent"
   so this endpoint can't be used to enumerate which companies are registered.
2. OAuth connect (``/auth/{provider}/authorize`` + ``/auth/{provider}/callback``)
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
    create_magic_link_token,
    create_session_token,
    create_state,
    get_or_create_member,
    get_user_by_email,
    resolve_org_for_email,
    save_connection,
    send_magic_link_email,
)
from ..config.settings import ApiSettings
from ..core.exceptions import AuthError, ConfigurationError, OAuthError
from .deps import SESSION_COOKIE_NAME, get_session

router = APIRouter(prefix="/auth", tags=["auth"])

_GENERIC_MAGIC_LINK_RESPONSE = {
    "message": "If that email is eligible, a sign-in link has been sent."
}


@router.post("/magic-link")
def request_magic_link(body: dict, settings: ApiSettings = Depends(ApiSettings.from_env)):
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    org_id = resolve_org_for_email(email)
    if org_id is not None:
        user = get_or_create_member(email, org_id)
        token = create_magic_link_token(email)
        base = (settings.frontend_url or "").rstrip("/")
        link = f"{base}/verify?token={token}"
        send_magic_link_email(email, link)
        _ = user  # created/reused; nothing further needed before the link is clicked

    # Always the same response — never reveal whether the domain is registered.
    return _GENERIC_MAGIC_LINK_RESPONSE


@router.get("/magic-link/verify")
def verify_magic_link(token: str):
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

    response = RedirectResponse(url="/")
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
