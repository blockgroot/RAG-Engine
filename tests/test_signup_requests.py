"""org_signup_requests queue: create/get + one-click approve/reject tokens (no HTTP)."""

from __future__ import annotations

import uuid

import pytest

from app.auth import (
    consume_approve_token,
    consume_reject_token,
    create_signup_request,
    get_pending_request_for_email,
    get_request_by_approve_token,
    get_request_by_reject_token,
)
from app.core.exceptions import AuthError

from .conftest import requires_db


def _new_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@newco.example.com"


@requires_db
def test_create_then_get_pending_round_trip(signup_email_cleanup):
    email = _new_email("create")
    signup_email_cleanup.append(email)

    created = create_signup_request(email, "Acme Co")
    assert created.status == "pending"
    assert created.company_name == "Acme Co"
    assert created.org_id is None
    assert created.approve_token
    assert created.reject_token

    fetched = get_pending_request_for_email(email)
    assert fetched is not None
    assert fetched.id == created.id


@requires_db
def test_duplicate_pending_request_is_rejected(signup_email_cleanup):
    email = _new_email("dup")
    signup_email_cleanup.append(email)

    create_signup_request(email, "First Co")
    with pytest.raises(AuthError):
        create_signup_request(email, "Second Co")


@requires_db
def test_reject_then_reapply_succeeds(signup_email_cleanup):
    email = _new_email("reapply")
    signup_email_cleanup.append(email)

    first = create_signup_request(email, "First Co")
    consume_reject_token(first.reject_token, reason="not a fit")

    # Re-submitting after rejection must succeed (only PENDING collides).
    second = create_signup_request(email, "Second Co")
    assert second.status == "pending"
    assert second.id != first.id


@requires_db
def test_get_request_by_approve_token_round_trip(signup_email_cleanup):
    email = _new_email("lookup")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "Lookup Co")
    fetched = get_request_by_approve_token(request.approve_token)
    assert fetched is not None
    assert fetched.id == request.id

    rejected_lookup = get_request_by_reject_token(request.reject_token)
    assert rejected_lookup is not None
    assert rejected_lookup.id == request.id


@requires_db
def test_approve_token_creates_org_and_admin(store, org_cleanup, signup_email_cleanup):
    email = _new_email("approve")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "Approved Co")
    approved, org_id = consume_approve_token(request.approve_token, store=store)
    org_cleanup.append(org_id)

    assert approved.status == "approved"
    assert approved.org_id == org_id

    from app.auth.users import get_user_by_email

    user = get_user_by_email(email)
    assert user is not None
    assert user.org_id == org_id
    assert user.role == "admin"


@requires_db
def test_approve_token_twice_raises_auth_error(store, org_cleanup, signup_email_cleanup):
    email = _new_email("approve-twice")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "Approve Twice Co")
    _, org_id = consume_approve_token(request.approve_token, store=store)
    org_cleanup.append(org_id)

    with pytest.raises(AuthError):
        consume_approve_token(request.approve_token, store=store)


@requires_db
def test_reject_token_records_reason(signup_email_cleanup):
    email = _new_email("reject")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "Rejected Co")
    rejected = consume_reject_token(
        request.reject_token, reason="duplicate of an existing tenant"
    )

    assert rejected.status == "rejected"
    assert rejected.reject_reason == "duplicate of an existing tenant"


@requires_db
def test_reject_token_twice_raises_auth_error(signup_email_cleanup):
    email = _new_email("reject-twice")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "Reject Twice Co")
    consume_reject_token(request.reject_token)

    with pytest.raises(AuthError):
        consume_reject_token(request.reject_token)


@requires_db
def test_approve_unknown_token_raises_auth_error(store):
    with pytest.raises(AuthError):
        consume_approve_token("not-a-real-token", store=store)


@requires_db
def test_approving_with_reject_token_fails(store, signup_email_cleanup):
    """The two tokens are not interchangeable — the reject token must never
    approve a request, even though both point at the same row."""
    email = _new_email("cross-token")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "Cross Token Co")
    with pytest.raises(AuthError):
        consume_approve_token(request.reject_token, store=store)
