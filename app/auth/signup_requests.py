"""Org-creation signup request queue.

Replaces immediate self-serve org+admin creation at ``/auth/signup``: a
brand-new company's first user lands here as ``pending`` instead of getting
an org synchronously. The platform owner reviews and approves/rejects via
``scripts/review_signup_requests.py`` — deliberately not a new HTTP/session
surface (see CLAUDE.md §2/§4). Only DB reads/writes live here; composing the
approval/rejection emails is the caller's job (mirrors ``users.py``'s
separation of concerns).
"""

from __future__ import annotations

import psycopg
from dataclasses import dataclass
from datetime import datetime

from ..core.exceptions import AuthError, NotFoundError
from ..db.connection import DatabaseError, get_connection
from .users import create_admin


@dataclass(frozen=True)
class SignupRequest:
    id: str
    email: str
    company_name: str
    status: str  # "pending" | "approved" | "rejected"
    reject_reason: str | None
    org_id: str | None
    created_at: datetime


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


def create_signup_request(email: str, company_name: str) -> SignupRequest:
    """Queue a new pending request for ``email``.

    Raises ``AuthError`` if a pending request for this email already exists
    (``idx_org_signup_requests_email_pending``) — re-submitting after a prior
    request was rejected is fine, since a rejected row no longer collides.
    """
    try:
        with get_connection() as conn:
            row = conn.execute(
                f"INSERT INTO org_signup_requests (email, company_name) "
                f"VALUES (%s, %s) RETURNING {_SELECT_COLUMNS}",
                (email.lower(), company_name),
            ).fetchone()
    except DatabaseError as exc:
        if isinstance(exc.cause, psycopg.errors.UniqueViolation):
            raise AuthError("A request for this email is already pending review.") from exc
        raise
    return _row_to_request(row)


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


def approve_signup_request(request_id: str, *, store) -> tuple[SignupRequest, str]:
    """Approve a pending request: create its org + admin user.

    ``store`` is a ``VectorStore`` (e.g. ``PgvectorStore``) used only for
    ``create_organization`` — kept as a parameter rather than constructed
    here so this module stays free of a hard dependency on one concrete
    store implementation, matching the rest of the app's factory-injection
    convention.

    Raises ``NotFoundError`` if no pending request matches ``request_id``
    (unknown id, or already approved/rejected).
    """
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE org_signup_requests SET status = 'approved', reviewed_at = now() "
            "WHERE id = %s::uuid AND status = 'pending' "
            "RETURNING email, company_name",
            (request_id,),
        ).fetchone()
    if row is None:
        raise NotFoundError("No pending request with that id.")
    email, company_name = row

    org_id = store.create_organization(company_name)
    create_admin(email, org_id)

    with get_connection() as conn:
        updated = conn.execute(
            f"UPDATE org_signup_requests SET org_id = %s::uuid WHERE id = %s::uuid "
            f"RETURNING {_SELECT_COLUMNS}",
            (org_id, request_id),
        ).fetchone()
    return _row_to_request(updated), org_id


def reject_signup_request(request_id: str, reason: str | None = None) -> SignupRequest:
    """Reject a pending request, optionally recording ``reason``.

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
