"""A member's own settings: "Linked accounts" (Second Brain step 1.2).

Member-level, scoped to ``(org_id, user_id)`` from the session like
``schedulers``: a link is about one person and nobody else can make, see or
remove it. A link changes who the knowledge graph credits, never what anyone
may read -- retrieval's access check does not look at these rows.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from ..auth import build_oauth_provider
from ..auth.github_oauth import GitHubAppProvider
from ..auth import email_change
from ..auth.email import send_email_change_verification_safe
from ..auth.oauth_state import LINK_GITHUB, consume_link_state, create_state
from ..auth.users import get_user
from ..config.settings import ApiSettings, EmailSettings, RateLimitSettings
from ..core.exceptions import AuthError, ConfigurationError, OAuthError
from ..security.rate_limit import check_rate_limit
from .validation import MAX_EMAIL_CHARS, bounded
from ..graph import identities
from .deps import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])

_ACCOUNT_PATH = "/account"


def _serialize(identity: identities.Identity) -> dict:
    return {
        "id": identity.id,
        "provider": identity.provider,
        "account": identity.external_id,
        "email": identity.email,
        "display_name": identity.display_name,
        "verified_by": identity.verified_by,
        # Only an OAuth link is the member's to remove; an email link would
        # simply come back on the next sync (see identities.unlink).
        "can_unlink": identity.verified_by == identities.VERIFIED_BY_OAUTH,
    }


def _on_identity_change(org_id: str) -> None:
    """Re-attribute graph edges after a link or unlink. Never fails the request."""
    try:
        from ..graph.builder import rebuild_people

        rebuild_people(org_id)
    except Exception:  # noqa: BLE001 - a stale graph is not a failed link
        logger.warning("account: graph rebuild after identity change failed", exc_info=True)


@router.get("/identities")
def list_identities(session=Depends(get_session)):
    try:
        # Cheap and org-scoped: a member who signed up after their Slack
        # identity was first seen is linked the moment they look.
        identities.auto_link_by_email(session.org_id)
    except Exception:  # noqa: BLE001 - listing must not depend on it
        logger.warning("account: auto-link on list failed", exc_info=True)
    linked = identities.list_for_user(session.org_id, session.user_id)
    github_available = True
    try:
        build_oauth_provider("github")
    except (OAuthError, ConfigurationError):
        github_available = False
    return {
        "identities": [_serialize(i) for i in linked],
        "github_link_available": github_available,
    }


@router.get("/identities/github/link")
def start_github_link(session=Depends(get_session)):
    """Send the member to GitHub to prove which account is theirs."""
    try:
        provider = build_oauth_provider("github")
    except (OAuthError, ConfigurationError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = create_state(session.org_id, LINK_GITHUB, user_id=session.user_id)
    return RedirectResponse(url=provider.authorize_url(state))


@router.delete("/identities/{identity_id}")
def remove_identity(identity_id: str, session=Depends(get_session)):
    if not identities.unlink(session.org_id, session.user_id, identity_id):
        raise HTTPException(status_code=404, detail="No linked account to remove")
    _on_identity_change(session.org_id)
    return {"ok": True}


def finish_github_link(provider, code: str, state: str, settings: ApiSettings) -> RedirectResponse:
    """Complete "Link your GitHub account" from the shared GitHub callback.

    The org and the PERSON come from the consumed state, never the request, so
    a forwarded callback URL can only ever link to whoever clicked. Any failure
    lands back on the settings page with a reason, not a raw 400: the member is
    standing in a browser tab, not calling an API.
    """
    base = (settings.frontend_url or "").rstrip("/")

    def back(query: str) -> RedirectResponse:
        return RedirectResponse(url=f"{base}{_ACCOUNT_PATH}?{query}")

    try:
        org_id, user_id = consume_link_state(state, provider=LINK_GITHUB)
        if not isinstance(provider, GitHubAppProvider):
            raise OAuthError("GitHub is not configured for account linking")
        login, name = provider.identify_user(code)
        identities.link_github(org_id, user_id, login, name)
    except OAuthError as exc:
        logger.info("account: GitHub link failed: %s", exc)
        return back("link_error=github")
    _on_identity_change(org_id)
    return back("linked=github")


# -- Personal memory (Second Brain layer C) ----------------------------------
# Member-level and scoped to (org_id, user_id) from the session: a fact is about
# one person, and nobody else can see, pin or delete it -- not even an admin.
# The admin's only control is the company-wide switch (`PUT /account/memory/org`).


def _fact(f) -> dict:
    return {
        "id": f.id,
        "kind": f.kind,
        "text": f.text,
        "pinned": f.pinned,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


@router.get("/memory")
def get_memory(session=Depends(get_session)):
    from ..config.settings import PersonalMemorySettings
    from ..memory import personal

    available = PersonalMemorySettings.from_env().enabled
    org_on, user_on = personal.switches(session.org_id, session.user_id)
    return {
        # Off for the deployment: the page says so instead of an empty list.
        "available": available,
        "org_enabled": org_on,
        "enabled": user_on,
        "can_manage_org": session.role == "admin",
        "facts": [_fact(f) for f in personal.list_facts(session.org_id, session.user_id)],
    }


@router.put("/memory/settings")
def set_memory_enabled(body: dict, session=Depends(get_session)):
    from ..memory import personal

    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be true or false")
    personal.set_user_enabled(session.org_id, session.user_id, enabled)
    return {"enabled": enabled}


@router.put("/memory/org")
def set_org_memory_enabled(body: dict, session=Depends(get_session)):
    from ..memory import personal

    if session.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="enabled must be true or false")
    personal.set_org_enabled(session.org_id, enabled)
    return {"org_enabled": enabled}


@router.post("/memory/{fact_id}/pin")
def pin_memory(fact_id: str, body: dict, session=Depends(get_session)):
    from ..memory import personal

    pinned = body.get("pinned")
    if not isinstance(pinned, bool):
        raise HTTPException(status_code=400, detail="pinned must be true or false")
    if not _is_uuid(fact_id) or not personal.set_pinned(session.org_id, session.user_id, fact_id, pinned):
        raise HTTPException(status_code=404, detail="No such memory")
    return {"id": fact_id, "pinned": pinned}


@router.delete("/memory/{fact_id}")
def delete_memory(fact_id: str, session=Depends(get_session)):
    from ..memory import personal

    if not _is_uuid(fact_id) or not personal.delete_fact(session.org_id, session.user_id, fact_id):
        raise HTTPException(status_code=404, detail="No such memory")
    return {"deleted": fact_id}


@router.delete("/memory")
def clear_memory(session=Depends(get_session)):
    from ..memory import personal

    return {"deleted": personal.clear_facts(session.org_id, session.user_id)}



# -- sign-in email ------------------------------------------------------------
# Changing it keeps the old address as a PRIOR email, so documents still shared
# with it stay readable (`auth/email_change.py`).


@router.get("/emails")
def list_emails(session=Depends(get_session)):
    user = get_user(session.user_id)
    return {
        "email": user.email if user else None,
        "prior": email_change.prior_emails(session.user_id),
    }


@router.post("/email")
def request_email_change(
    body: dict,
    request: Request,
    background_tasks: BackgroundTasks,
    session=Depends(get_session),
):
    """Mail a confirmation link to the NEW address. Nothing changes until it is used.

    The link goes to the new inbox, so the change proves both halves: the
    session proves the account, the link proves the address.
    """
    email = bounded(
        (body.get("email") or "").strip().lower(), field="Email", limit=MAX_EMAIL_CHARS
    )
    check_rate_limit(
        f"email-change:{session.user_id}",
        limit=RateLimitSettings.from_env().auth_requests_per_window,
    )
    try:
        token = email_change.request_email_change(session.user_id, email)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    link = f"{str(request.base_url).rstrip('/')}/auth/email-change/confirm?token={token}"
    background_tasks.add_task(send_email_change_verification_safe, email, link)
    return {
        "status": "sent",
        "message": f"We sent a confirmation link to {email}. Your email changes when you use it.",
        # Same console-mode echo as the magic link: with no real mail going out
        # there is no inbox to find the link in.
        "dev_link": link if EmailSettings.from_env().sender == "console" else None,
    }


@router.delete("/emails/{email}")
def remove_prior_email(email: str, session=Depends(get_session)):
    """Forget a prior address. Documents shared ONLY with it stop matching."""
    if not email_change.remove_prior_email(session.user_id, email):
        raise HTTPException(status_code=404, detail="No such prior email.")
    return {"removed": email.strip().lower()}


def _is_uuid(value: str) -> bool:
    import uuid as _uuid

    try:
        _uuid.UUID(value)
        return True
    except (ValueError, TypeError):
        return False
