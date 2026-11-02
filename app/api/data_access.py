"""Admin › Data access: the company's read-only database login.

Admin-only, ``org_id`` from the session alone. The password is in the reply
that creates or resets the login and nowhere else, so that reply is no-store.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from ..config.settings import TenantAccessSettings
from ..tenantaccess import (
    LoginExistsError,
    ReaderCredentials,
    create_login,
    drop_login,
    get_login,
    reset_password,
    set_allowed_ips,
)
from .deps import SessionClaims, require_admin

router = APIRouter(prefix="/admin/data-access", tags=["admin"])


def _enabled_settings() -> TenantAccessSettings:
    settings = TenantAccessSettings.from_env()
    if not settings.enabled:
        raise HTTPException(status_code=400, detail="Direct database access is not available")
    return settings


def _credentials(creds: ReaderCredentials, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    return {"role_name": creds.role_name, "password": creds.password, "uri": creds.uri}


@router.get("")
def get_data_access(session: SessionClaims = Depends(require_admin)):
    login = get_login(session.org_id)
    return {
        "enabled": TenantAccessSettings.from_env().enabled,
        "login": None
        if login is None
        else {
            "role_name": login.role_name,
            "allowed_ips": login.allowed_ips,
            "created_at": login.created_at.isoformat(),
        },
    }


@router.post("")
def create_data_access(
    body: dict, response: Response, session: SessionClaims = Depends(require_admin)
):
    """Create the login. The admin must accept that it reads every document.

    Handbook shows each person only what is shared with them; this login sees
    all of the company's indexed text, private documents included.
    """
    settings = _enabled_settings()
    if body.get("acknowledge_all_documents") is not True:
        raise HTTPException(
            status_code=400,
            detail="Confirm that this login can read all of the company's indexed "
            "data, including private documents",
        )
    try:
        creds = create_login(
            session.org_id, body.get("allowed_ips"), session.user_id, settings
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LoginExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _credentials(creds, response)


@router.put("/ips")
def put_data_access_ips(body: dict, session: SessionClaims = Depends(require_admin)):
    try:
        changed = set_allowed_ips(session.org_id, body.get("allowed_ips"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not changed:
        raise HTTPException(status_code=404, detail="No database login")
    return {"allowed_ips": get_login(session.org_id).allowed_ips}


@router.post("/reset-password")
def reset_data_access_password(
    response: Response, session: SessionClaims = Depends(require_admin)
):
    creds = reset_password(session.org_id, _enabled_settings())
    if creds is None:
        raise HTTPException(status_code=404, detail="No database login")
    return _credentials(creds, response)


@router.delete("")
def delete_data_access(session: SessionClaims = Depends(require_admin)):
    """Revoke. Idempotent: ``deleted: false`` when there was no login."""
    return {"deleted": drop_login(session.org_id)}
