"""A member's own settings: "Linked accounts" (Second Brain step 1.2).

Member-level, scoped to ``(org_id, user_id)`` from the session like
``schedulers``: a link is about one person and nobody else can make, see or
remove it. A link changes who the knowledge graph credits, never what anyone
may read -- retrieval's access check does not look at these rows.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from ..auth import build_oauth_provider
from ..auth.github_oauth import GitHubAppProvider
from ..auth.oauth_state import LINK_GITHUB, consume_link_state, create_state
from ..config.settings import ApiSettings
from ..core.exceptions import ConfigurationError, OAuthError
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
