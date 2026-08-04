"""owner_email_whitelist: add/remove/list/is_whitelisted (no HTTP)."""

from __future__ import annotations

import uuid

import pytest

from app.auth import add_owner_email, is_whitelisted, list_owner_emails, remove_owner_email

from .conftest import requires_db


def _new_email(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}@owner.example.com"


@requires_db
def test_add_then_is_whitelisted(whitelist_cleanup):
    email = _new_email("add")
    whitelist_cleanup.append(email)

    assert is_whitelisted(email) is False
    add_owner_email(email)
    assert is_whitelisted(email) is True


@requires_db
def test_is_whitelisted_false_for_never_added_email():
    assert is_whitelisted(_new_email("never")) is False


@requires_db
def test_remove_then_not_whitelisted(whitelist_cleanup):
    email = _new_email("remove")
    whitelist_cleanup.append(email)

    add_owner_email(email)
    assert is_whitelisted(email) is True
    remove_owner_email(email)
    assert is_whitelisted(email) is False


@requires_db
def test_add_twice_is_idempotent(whitelist_cleanup):
    email = _new_email("dup")
    whitelist_cleanup.append(email)

    add_owner_email(email)
    add_owner_email(email)  # must not raise
    assert is_whitelisted(email) is True


@requires_db
def test_remove_never_added_email_does_not_raise():
    remove_owner_email(_new_email("gone"))  # no-op, no exception


@requires_db
def test_list_owner_emails_reflects_add_and_remove(whitelist_cleanup):
    email = _new_email("list")
    whitelist_cleanup.append(email)

    assert email not in list_owner_emails()
    add_owner_email(email)
    assert email in list_owner_emails()
    remove_owner_email(email)
    assert email not in list_owner_emails()


@requires_db
def test_email_matching_is_case_insensitive(whitelist_cleanup):
    email = _new_email("case")
    whitelist_cleanup.append(email)

    add_owner_email(email.upper())
    assert is_whitelisted(email) is True
