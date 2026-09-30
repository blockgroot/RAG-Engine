"""Changing your sign-in email keeps your document access (`auth/email_change.py`).

Access is keyed on EMAIL, so a changed address used to stop matching every
grant Drive/Slack/Linear still record under the old one. These pin the verified
change flow, the prior-email alias it leaves behind, and the two rules that
keep an alias safe: every one was proven, and an address belongs to one person.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.auth import create_session_token, email_change, get_user_by_email, invite_member
from app.sources.google_groups import viewer_for_person
from app.vectorstore.base import Viewer

from .conftest import requires_db


def test_acl_carries_prior_emails_and_their_domains():
    viewer = Viewer(email="Ada@New.com", aliases=("ada@old.com", "ADA@new.com"))
    assert viewer.acl() == ["ada@new.com", "domain:new.com", "ada@old.com", "domain:old.com"]


def test_a_public_only_viewer_ignores_aliases():
    """An alias belongs to an identity; no identity, nothing to honour."""
    assert Viewer(email=None, public_only=True, aliases=("ada@old.com",)).acl() == []


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("EMAIL_SENDER", "console")
    monkeypatch.setenv("FRONTEND_URL", "https://portal.example.com")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://portal.example.com")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


@pytest.fixture
def member(store, org_cleanup):
    org_id = store.create_organization("Email Change Test Org")
    org_cleanup.append(org_id)
    email = f"old-{uuid.uuid4().hex[:8]}@old.example.com"
    user = invite_member(email, org_id)
    return org_id, user, {"session": create_session_token(user)}


def _token(link: str) -> str:
    return parse_qs(urlparse(link).query)["token"][0]


def _request(client, cookies, email):
    return client.post("/account/email", json={"email": email}, cookies=cookies)


@requires_db
def test_a_confirmed_change_moves_the_login_and_keeps_the_old_address(client, member):
    org_id, user, cookies = member
    new = f"new-{uuid.uuid4().hex[:8]}@new.example.com"

    response = _request(client, cookies, new)
    assert response.status_code == 200
    token = _token(response.json()["dev_link"])

    # GET only shows what WOULD change: a mail scanner must not move the account.
    page = client.get(f"/auth/email-change/confirm?token={token}")
    assert page.status_code == 200 and new in page.text and user.email in page.text
    assert get_user_by_email(user.email) is not None

    done = client.post("/auth/email-change/confirm", data={"token": token})
    assert "is now" in done.text
    assert get_user_by_email(user.email) is None
    assert get_user_by_email(new).id == user.id

    # The point of the feature: grants to the OLD address still match.
    acl = viewer_for_person(org_id, new).acl()
    assert user.email in acl and new in acl
    assert client.get("/account/emails", cookies=cookies).json() == {
        "email": new, "prior": [user.email],
    }


@requires_db
def test_the_link_is_single_use(client, member):
    _, _, cookies = member
    token = _token(_request(client, cookies, f"n-{uuid.uuid4().hex[:8]}@x.example.com").json()["dev_link"])
    client.post("/auth/email-change/confirm", data={"token": token})
    again = client.post("/auth/email-change/confirm", data={"token": token})
    assert "invalid, expired, or already used" in again.text


@requires_db
def test_a_newer_request_voids_the_older_link(client, member):
    _, user, cookies = member
    first = _token(_request(client, cookies, f"a-{uuid.uuid4().hex[:8]}@x.example.com").json()["dev_link"])
    _request(client, cookies, f"b-{uuid.uuid4().hex[:8]}@x.example.com")
    stale = client.post("/auth/email-change/confirm", data={"token": first})
    assert "invalid, expired, or already used" in stale.text
    assert get_user_by_email(user.email) is not None


@requires_db
def test_an_address_another_account_signs_in_with_is_refused(client, member, store, org_cleanup):
    org_id, _, cookies = member
    other = f"taken-{uuid.uuid4().hex[:8]}@x.example.com"
    invite_member(other, org_id)
    response = _request(client, cookies, other)
    assert response.status_code == 400
    assert "already uses" in response.json()["detail"]


@requires_db
def test_a_reassigned_old_address_stops_being_an_alias(client, member):
    """When somebody else starts signing in with your old address, grants to it
    are theirs -- the alias must stop matching for you."""
    org_id, user, cookies = member
    new = f"new-{uuid.uuid4().hex[:8]}@x.example.com"
    token = _token(_request(client, cookies, new).json()["dev_link"])
    client.post("/auth/email-change/confirm", data={"token": token})
    assert email_change.aliases_for(org_id, new) == (user.email,)

    invite_member(user.email, org_id)  # the address now belongs to someone else
    assert email_change.aliases_for(org_id, new) == ()


@requires_db
def test_aliases_never_cross_tenants(client, member):
    org_id, user, cookies = member
    new = f"new-{uuid.uuid4().hex[:8]}@x.example.com"
    token = _token(_request(client, cookies, new).json()["dev_link"])
    client.post("/auth/email-change/confirm", data={"token": token})
    assert email_change.aliases_for(str(uuid.uuid4()), new) == ()


@requires_db
def test_a_prior_email_can_be_forgotten(client, member):
    _, user, cookies = member
    new = f"new-{uuid.uuid4().hex[:8]}@x.example.com"
    token = _token(_request(client, cookies, new).json()["dev_link"])
    client.post("/auth/email-change/confirm", data={"token": token})

    assert client.delete(f"/account/emails/{user.email}", cookies=cookies).status_code == 200
    assert client.get("/account/emails", cookies=cookies).json()["prior"] == []
    assert client.delete(f"/account/emails/{user.email}", cookies=cookies).status_code == 404


@requires_db
def test_a_connector_identity_stays_linked_after_the_change(client, member):
    """Drive still reports the OLD address for this person. Their graph identity
    must not be un-linked just because they changed how they sign in."""
    from app.db.connection import get_connection
    from app.graph import identities

    org_id, user, cookies = member
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO person_identities (org_id, provider, external_id, email) "
            "VALUES (%s::uuid, 'google', %s, %s)",
            (org_id, f"email:{user.email}", user.email),
        )
    assert identities.auto_link_by_email(org_id) == 1

    new = f"new-{uuid.uuid4().hex[:8]}@x.example.com"
    token = _token(_request(client, cookies, new).json()["dev_link"])
    client.post("/auth/email-change/confirm", data={"token": token})
    identities.auto_link_by_email(org_id)

    with get_connection() as conn:
        linked = conn.execute(
            "SELECT user_id::text FROM person_identities WHERE org_id = %s::uuid",
            (org_id,),
        ).fetchone()[0]
    assert linked == user.id
