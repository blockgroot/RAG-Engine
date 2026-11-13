"""Org-creation signup request queue.

A new company's first user lands here as ``pending``. The platform owner
approves or rejects only through single-use links in the notification email
(possession tokens, like ``magic_link.py``). DB only; callers compose emails.
"""

from __future__ import annotations

import hashlib
import secrets
import psycopg
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config.settings import AuthSettings
from ..core.exceptions import AuthError
from ..db.connection import DatabaseError, get_connection
from .users import create_admin

_TOKEN_BYTES = 32


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class SignupRequest:
    id: str
    email: str
    company_name: str
    status: str  # "pending" | "approved" | "rejected"
    reject_reason: str | None
    org_id: str | None
    created_at: datetime
    # Set only by create_signup_request; only the SHA-256 hashes are stored.
    approve_token: str | None = None
    reject_token: str | None = None


_SELECT_COLUMNS = (
    "id::text, email, company_name, status, reject_reason, org_id::text, created_at"
)


def _row_to_request(row) -> SignupRequest:
    return SignupRequest(
        id=row[0],
        email=row[1],
        company_name=row[2],
        status=row[3],
        reject_reason=row[4],
        org_id=row[5],
        created_at=row[6],
    )


def create_signup_request(
    email: str, company_name: str, *, settings: AuthSettings | None = None
) -> SignupRequest:
    """Queue a pending request for ``email`` and mint its approve/reject tokens.

    The raw tokens are returned once (``.approve_token``/``.reject_token``);
    only hashes are stored. Raises ``AuthError`` if a pending request for the
    email exists; one after a rejection is fine.
    """
    settings = settings or AuthSettings.from_env()
    approve_token = secrets.token_urlsafe(_TOKEN_BYTES)
    reject_token = secrets.token_urlsafe(_TOKEN_BYTES)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.signup_action_ttl_hours)
    try:
        with get_connection() as conn:
            row = conn.execute(
                f"INSERT INTO org_signup_requests "
                f"(email, company_name, approve_token_hash, reject_token_hash, action_expires_at) "
                f"VALUES (%s, %s, %s, %s, %s) RETURNING {_SELECT_COLUMNS}",
                (
                    email.lower(),
                    company_name,
                    _hash(approve_token),
                    _hash(reject_token),
                    expires_at,
                ),
            ).fetchone()
    except DatabaseError as exc:
        if isinstance(exc.cause, psycopg.errors.UniqueViolation):
            raise AuthError("A request for this email is already pending review.") from exc
        raise
    request = _row_to_request(row)
    return SignupRequest(
        **{**request.__dict__, "approve_token": approve_token, "reject_token": reject_token}
    )


def get_pending_request_for_email(email: str) -> SignupRequest | None:
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM org_signup_requests "
            f"WHERE email = %s AND status = 'pending'",
            (email.lower(),),
        ).fetchone()
    return _row_to_request(row) if row else None


def get_request_by_approve_token(token: str) -> SignupRequest | None:
    """Read-only lookup for the GET confirmation page — never mutates state.
    Returns ``None`` if the token is unknown, expired, or the request is no
    longer pending."""
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM org_signup_requests "
            "WHERE approve_token_hash = %s AND status = 'pending' AND action_expires_at > now()",
            (_hash(token),),
        ).fetchone()
    return _row_to_request(row) if row else None


def get_request_by_reject_token(token: str) -> SignupRequest | None:
    """Read-only lookup for the GET confirmation page — never mutates state."""
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM org_signup_requests "
            "WHERE reject_token_hash = %s AND status = 'pending' AND action_expires_at > now()",
            (_hash(token),),
        ).fetchone()
    return _row_to_request(row) if row else None


def consume_approve_token(token: str, *, store) -> tuple[SignupRequest, str]:
    """Approve a pending request by its email token, then create its org and admin.

    ``store`` is passed in for ``create_organization`` (factory injection).
    Raises ``AuthError`` if the token is unknown, expired or no longer pending.
    """
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE org_signup_requests SET status = 'approved', reviewed_at = now() "
            "WHERE approve_token_hash = %s AND status = 'pending' AND action_expires_at > now() "
            "RETURNING id::text, email, company_name",
            (_hash(token),),
        ).fetchone()
    if row is None:
        raise AuthError("This approval link is invalid, expired, or already used.")
    request_id, email, company_name = row

    org_id = store.create_organization(company_name)
    create_admin(email, org_id)

    with get_connection() as conn:
        updated = conn.execute(
            f"UPDATE org_signup_requests SET org_id = %s::uuid WHERE id = %s::uuid "
            f"RETURNING {_SELECT_COLUMNS}",
            (org_id, request_id),
        ).fetchone()
    return _row_to_request(updated), org_id


def consume_reject_token(token: str, reason: str | None = None) -> SignupRequest:
    """Reject a pending request by its email token.

    Raises ``AuthError`` if the token is unknown, expired or no longer pending.
    """
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE org_signup_requests "
            "SET status = 'rejected', reject_reason = %s, reviewed_at = now() "
            "WHERE reject_token_hash = %s AND status = 'pending' AND action_expires_at > now() "
            f"RETURNING {_SELECT_COLUMNS}",
            (reason, _hash(token)),
        ).fetchone()
    if row is None:
        raise AuthError("This rejection link is invalid, expired, or already used.")
    return _row_to_request(row)
