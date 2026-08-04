"""Org-creation signup request queue.

Replaces immediate self-serve org+admin creation at ``/auth/signup``: a
brand-new company's first user lands here as ``pending`` instead of getting
an org synchronously. The platform owner reviews and approves/rejects either
via ``scripts/review_signup_requests.py`` (by id) or the one-click links in
the owner-notification email (``consume_approve_token``/
``consume_reject_token``, by possession token) — deliberately not a new
authenticated HTTP/session surface, just a bearer-token action link like
``magic_link.py`` (see CLAUDE.md §2/§4). Only DB reads/writes live here;
composing the approval/rejection/notification emails is the caller's job
(mirrors ``users.py``'s separation of concerns).
"""

from __future__ import annotations

import hashlib
import secrets
import psycopg
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config.settings import AuthSettings
from ..core.exceptions import AuthError, NotFoundError
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
    # Only ever populated by create_signup_request, right after the raw
    # tokens are minted — never recoverable from a DB read (only their
    # SHA-256 hashes are stored, same trust model as magic_link_tokens).
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
    """Queue a new pending request for ``email``.

    Also mints a pair of single-use possession tokens for the one-click
    email approve/reject links, valid for ``AuthSettings.signup_action_ttl_hours``.
    The raw tokens are returned on ``.approve_token``/``.reject_token`` of the
    result (only this once — only their hashes are stored); the caller
    (``POST /auth/signup``) uses them to build the owner-notification email.

    Raises ``AuthError`` if a pending request for this email already exists
    (``idx_org_signup_requests_email_pending``) — re-submitting after a prior
    request was rejected is fine, since a rejected row no longer collides.
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


def list_signup_requests(status: str | None = "pending") -> list[SignupRequest]:
    """List signup requests. ``status=None`` lists every request, any status."""
    with get_connection() as conn:
        if status is None:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM org_signup_requests ORDER BY created_at"
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_SELECT_COLUMNS} FROM org_signup_requests "
                f"WHERE status = %s ORDER BY created_at",
                (status,),
            ).fetchall()
    return [_row_to_request(row) for row in rows]


def _approve_by_clause(where_clause: str, param, *, store) -> tuple[SignupRequest, str]:
    """Shared approve logic: mark approved, create org + admin, keyed by
    whatever ``where_clause``/``param`` uniquely identifies one pending row
    (an id, or a hashed possession token — see ``approve_signup_request`` /
    ``consume_approve_token``). ``store`` is passed straight through to
    ``create_organization`` — never a module-level global, so concurrent
    approvals (by id and by token) can't race on each other's store.

    Raises ``NotFoundError`` if no pending row matches.
    """
    with get_connection() as conn:
        row = conn.execute(
            f"UPDATE org_signup_requests SET status = 'approved', reviewed_at = now() "
            f"WHERE {where_clause} AND status = 'pending' "
            f"RETURNING id::text, email, company_name",
            param,
        ).fetchone()
    if row is None:
        raise NotFoundError("No pending request found for that reference.")
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


def approve_signup_request(request_id: str, *, store) -> tuple[SignupRequest, str]:
    """Approve a pending request by id: create its org + admin user.

    ``store`` is a ``VectorStore`` (e.g. ``PgvectorStore``) used only for
    ``create_organization`` — kept as a parameter rather than constructed
    here so this module stays free of a hard dependency on one concrete
    store implementation, matching the rest of the app's factory-injection
    convention. Used by ``scripts/review_signup_requests.py``.

    Raises ``NotFoundError`` if no pending request matches ``request_id``
    (unknown id, or already approved/rejected).
    """
    return _approve_by_clause("id = %s::uuid", (request_id,), store=store)


def reject_signup_request(request_id: str, reason: str | None = None) -> SignupRequest:
    """Reject a pending request by id, optionally recording ``reason``.

    Raises ``NotFoundError`` if no pending request matches ``request_id``.
    """
    with get_connection() as conn:
        row = conn.execute(
            f"UPDATE org_signup_requests "
            f"SET status = 'rejected', reject_reason = %s, reviewed_at = now() "
            f"WHERE id = %s::uuid AND status = 'pending' "
            f"RETURNING {_SELECT_COLUMNS}",
            (reason, request_id),
        ).fetchone()
    if row is None:
        raise NotFoundError("No pending request with that id.")
    return _row_to_request(row)


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
    """Approve a pending request via its one-click email possession token.

    Same guarantees as ``approve_signup_request`` (atomic pending-only
    transition, org+admin creation) but keyed by a hashed, expiring token
    instead of an id — used by the ``/auth/signup-requests/approve`` route
    reached from the owner-notification email. Raises ``AuthError`` if the
    token is unknown, expired, or the request is no longer pending (already
    approved/rejected, or a stale duplicate email link).
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id::text FROM org_signup_requests "
            "WHERE approve_token_hash = %s AND status = 'pending' AND action_expires_at > now()",
            (_hash(token),),
        ).fetchone()
    if row is None:
        raise AuthError("This approval link is invalid, expired, or already used.")
    return _approve_by_clause("id = %s::uuid", (row[0],), store=store)


def consume_reject_token(token: str, reason: str | None = None) -> SignupRequest:
    """Reject a pending request via its one-click email possession token.

    Raises ``AuthError`` if the token is unknown, expired, or the request is
    no longer pending.
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
