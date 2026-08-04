"""org_signup_requests queue: create/get/list/approve/reject (no HTTP)."""

from __future__ import annotations

import uuid

import pytest

from app.auth import (
    approve_signup_request,
    create_signup_request,
    get_pending_request_for_email,
    list_signup_requests,
    reject_signup_request,
)
from app.core.exceptions import AuthError, NotFoundError

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
    reject_signup_request(first.id, reason="not a fit")

    # Re-submitting after rejection must succeed (only PENDING collides).
    second = create_signup_request(email, "Second Co")
    assert second.status == "pending"
    assert second.id != first.id


@requires_db
def test_approve_creates_org_and_admin(store, org_cleanup, signup_email_cleanup):
    email = _new_email("approve")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "Approved Co")
    approved, org_id = approve_signup_request(request.id, store=store)
    org_cleanup.append(org_id)

    assert approved.status == "approved"
    assert approved.org_id == org_id

    from app.auth.users import get_user_by_email

    user = get_user_by_email(email)
    assert user is not None
    assert user.org_id == org_id
    assert user.role == "admin"


@requires_db
def test_approve_twice_raises_not_found(store, org_cleanup, signup_email_cleanup):
    email = _new_email("approve-twice")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "Approve Twice Co")
    _, org_id = approve_signup_request(request.id, store=store)
    org_cleanup.append(org_id)

    with pytest.raises(NotFoundError):
        approve_signup_request(request.id, store=store)


@requires_db
def test_reject_records_reason(signup_email_cleanup):
    email = _new_email("reject")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "Rejected Co")
    rejected = reject_signup_request(request.id, reason="duplicate of an existing tenant")

    assert rejected.status == "rejected"
    assert rejected.reject_reason == "duplicate of an existing tenant"


@requires_db
def test_reject_twice_raises_not_found(signup_email_cleanup):
    email = _new_email("reject-twice")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "Reject Twice Co")
    reject_signup_request(request.id)

    with pytest.raises(NotFoundError):
        reject_signup_request(request.id)


@requires_db
def test_approve_unknown_id_raises_not_found(store):
    with pytest.raises(NotFoundError):
        approve_signup_request(str(uuid.uuid4()), store=store)


@requires_db
def test_list_signup_requests_pending_vs_all(signup_email_cleanup):
    email = _new_email("list")
    signup_email_cleanup.append(email)

    request = create_signup_request(email, "List Co")
    reject_signup_request(request.id, reason="test")

    pending = list_signup_requests(status="pending")
    assert request.id not in {r.id for r in pending}

    everything = list_signup_requests(status=None)
    assert request.id in {r.id for r in everything}
    matched = next(r for r in everything if r.id == request.id)
    assert matched.status == "rejected"
