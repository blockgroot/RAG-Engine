"""Signed session tokens (Phase 13).

A session is a short-lived JWT carrying ``sub`` (user id), ``org_id``, and
``role`` — issued ONLY after a user's org is already resolved (magic-link
verify, or an admin's own org at signup). There is no authenticated state with
a null ``org_id``: every request handler trusts the session's ``org_id``
exclusively, never a header/body/query value, so a request can only ever act
on the tenant baked into its own signed session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from ..config.settings import AuthSettings
from ..core.exceptions import AuthError, ConfigurationError
from .users import User

_ALGORITHM = "HS256"


@dataclass(frozen=True)
class SessionClaims:
    user_id: str
    org_id: str
    role: str
    issued_at: datetime


def create_session_token(user: User, *, settings: AuthSettings | None = None) -> str:
    """Issue a session JWT for ``user``. Raises if ``user.org_id`` is unset —
    a session must never be issued for an org-less user."""
    settings = settings or AuthSettings.from_env()
    if not settings.jwt_secret:
        raise ConfigurationError("AUTH_JWT_SECRET must be set to issue sessions")
    if not user.org_id:
        raise AuthError("Cannot issue a session for a user with no resolved organization")

    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "org_id": user.org_id,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=settings.session_ttl_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_session_token(token: str, *, settings: AuthSettings | None = None) -> SessionClaims:
    """Validate and decode a session JWT. Raises ``AuthError`` if invalid/expired."""
    settings = settings or AuthSettings.from_env()
    if not settings.jwt_secret:
        raise ConfigurationError("AUTH_JWT_SECRET must be set to verify sessions")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise AuthError("Invalid or expired session") from exc

    org_id = payload.get("org_id")
    if not org_id:
        raise AuthError("Session is missing a resolved organization")
    raw_iat = payload.get("iat")
    if isinstance(raw_iat, (int, float)):
        issued_at = datetime.fromtimestamp(raw_iat, tz=timezone.utc)
    else:
        issued_at = datetime.now(timezone.utc)
    return SessionClaims(
        user_id=payload["sub"],
        org_id=org_id,
        role=payload.get("role", "member"),
        issued_at=issued_at,
    )
