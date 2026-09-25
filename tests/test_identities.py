"""Second Brain 1.2: identity linking.

The plan's "done when", each pinned directly:

* two identities with the SAME display name never merge;
* an email matching a member of ANOTHER org never links;
* nothing links without OAuth or a verified (login) email;
* unlinking returns the identity to unlinked (the graph re-attributes it).

Plus the flow's own safety: the GitHub link attaches to whoever STARTED it
(the person is read from the consumed state, never the request), and a link
state can never complete a connect flow or the other way round.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import create_session_token
from app.auth.users import invite_member
from app.db.connection import get_connection
from app.graph import identities
from app.sources.meta import person

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("EMAIL_SENDER", "console")
    monkeypatch.setenv("API_CORS_ORIGINS", "https://portal.example.com")
    monkeypatch.setenv("FRONTEND_URL", "https://portal.example.com")
    monkeypatch.setenv("GITHUB_APP_SLUG", "handbook-test")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")


def _org(store, org_cleanup):
    org_id = store.create_organization(f"Identity Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    return org_id


def _rows(org_id):
    with get_connection() as conn:
        return {
            (r[0], r[1]): (r[2], r[3])
            for r in conn.execute(
                "SELECT provider, external_id, user_id::text, verified_by "
                "FROM person_identities WHERE org_id = %s::uuid",
                (org_id,),
            ).fetchall()
        }


# -- the store ----------------------------------------------------------------


def test_external_id_matches_the_meta_key():
    assert identities.identity_external_id(person("slack", role="author", external_id="U1")) == "U1"
    assert (
        identities.identity_external_id(person("google", role="editor", email="a@x.com"))
        == "email:a@x.com"
    )


@requires_db
def test_two_people_with_one_name_stay_two_identities(store, org_cleanup):
    org_id = _org(store, org_cleanup)
    member = invite_member(f"priya-{uuid.uuid4().hex[:6]}@example.com", org_id)
    identities.upsert_identities(
        org_id,
        [
            person("slack", role="author", external_id="U1", name="Priya"),
            person("slack", role="author", external_id="U2", name="Priya", email=member.email),
        ],
    )
    identities.auto_link_by_email(org_id)
    rows = _rows(org_id)
    assert rows[("slack", "U1")] == (None, None)
    assert rows[("slack", "U2")] == (member.id, identities.VERIFIED_BY_EMAIL)


@requires_db
def test_an_email_from_another_org_never_links(store, org_cleanup):
    org_a = _org(store, org_cleanup)
    org_b = _org(store, org_cleanup)
    outsider = invite_member(f"shared-{uuid.uuid4().hex[:6]}@example.com", org_b)
    identities.upsert_identities(
        org_a, [person("slack", role="author", external_id="U9", email=outsider.email)]
    )
    assert identities.auto_link_by_email(org_a) == 0
    assert _rows(org_a)[("slack", "U9")] == (None, None)


@requires_db
def test_an_email_link_follows_the_email(store, org_cleanup):
    org_id = _org(store, org_cleanup)
    member = invite_member(f"ada-{uuid.uuid4().hex[:6]}@example.com", org_id)
    identities.upsert_identities(
        org_id, [person("slack", role="author", external_id="U1", email=member.email.upper())]
    )
    assert identities.auto_link_by_email(org_id) == 1
    assert _rows(org_id)[("slack", "U1")][0] == member.id

    # The connector now reports a different address: the proof is gone.
    identities.upsert_identities(
        org_id, [person("slack", role="author", external_id="U1", email="someone-else@example.com")]
    )
    identities.auto_link_by_email(org_id)
    assert _rows(org_id)[("slack", "U1")] == (None, None)


@requires_db
def test_a_sync_that_omits_the_email_keeps_the_known_one(store, org_cleanup):
    org_id = _org(store, org_cleanup)
    identities.upsert_identities(
        org_id, [person("slack", role="author", external_id="U1", email="a@example.com")]
    )
    identities.upsert_identities(org_id, [person("slack", role="mentioned", external_id="U1")])
    with get_connection() as conn:
        email = conn.execute(
            "SELECT email FROM person_identities WHERE org_id = %s::uuid", (org_id,)
        ).fetchone()[0]
    assert email == "a@example.com"


@requires_db
def test_oauth_link_moves_to_whoever_proves_it_and_only_they_unlink(store, org_cleanup):
    org_id = _org(store, org_cleanup)
    ada = invite_member(f"ada-{uuid.uuid4().hex[:6]}@example.com", org_id)
    bo = invite_member(f"bo-{uuid.uuid4().hex[:6]}@example.com", org_id)

    first = identities.link_github(org_id, ada.id, "Ada-Dev", "Ada")
    assert (first.external_id, first.user_id, first.verified_by) == ("ada-dev", ada.id, "oauth")

    identities.link_github(org_id, bo.id, "ada-dev", None)
    assert _rows(org_id)[("github", "ada-dev")] == (bo.id, "oauth")

    assert identities.unlink(org_id, ada.id, first.id) is False  # not Ada's any more
    assert identities.unlink(org_id, bo.id, first.id) is True
    assert _rows(org_id)[("github", "ada-dev")] == (None, None)


@requires_db
def test_an_email_link_is_not_unlinkable(store, org_cleanup):
    org_id = _org(store, org_cleanup)
    member = invite_member(f"ada-{uuid.uuid4().hex[:6]}@example.com", org_id)
    identities.upsert_identities(
        org_id, [person("slack", role="author", external_id="U1", email=member.email)]
    )
    identities.auto_link_by_email(org_id)
    [linked] = identities.list_for_user(org_id, member.id)
    assert identities.unlink(org_id, member.id, linked.id) is False


# -- the HTTP flow ---------------------------------------------------------------


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


@requires_db
def test_github_link_flow_attaches_to_whoever_started_it(client, store, org_cleanup, monkeypatch):
    org_id = _org(store, org_cleanup)
    member = invite_member(f"ada-{uuid.uuid4().hex[:6]}@example.com", org_id)
    cookies = {"session": create_session_token(member)}

    start = client.get("/account/identities/github/link", cookies=cookies, follow_redirects=False)
    assert start.status_code in (302, 307)
    state = start.headers["location"].split("state=")[1].split("&")[0]

    monkeypatch.setattr(
        "app.auth.github_oauth.GitHubAppProvider.identify_user",
        lambda self, code: ("Ada-GH", "Ada"),
    )
    done = client.get(
        f"/auth/github/callback?code=abc&state={state}", follow_redirects=False
    )
    assert done.status_code in (302, 307)
    assert done.headers["location"] == "https://portal.example.com/account?linked=github"
    assert _rows(org_id)[("github", "ada-gh")] == (member.id, "oauth")

    # Single use: the same callback URL again links nothing more.
    again = client.get(f"/auth/github/callback?code=abc&state={state}", follow_redirects=False)
    assert "link_error=github" in again.headers["location"]

    listed = client.get("/account/identities", cookies=cookies).json()
    assert [(i["provider"], i["account"], i["can_unlink"]) for i in listed["identities"]] == [
        ("github", "ada-gh", True)
    ]
    removed = client.delete(f"/account/identities/{listed['identities'][0]['id']}", cookies=cookies)
    assert removed.status_code == 200
    assert client.get("/account/identities", cookies=cookies).json()["identities"] == []


@requires_db
def test_a_connect_state_cannot_complete_a_link(client, store, org_cleanup, monkeypatch):
    from app.auth.oauth_state import consume_link_state, create_state
    from app.core.exceptions import OAuthError

    org_id = _org(store, org_cleanup)
    connect_state = create_state(org_id, "github")
    with pytest.raises(OAuthError):
        consume_link_state(connect_state, provider="github_link")


@requires_db
def test_unlinking_someone_elses_identity_is_404(client, store, org_cleanup):
    org_id = _org(store, org_cleanup)
    ada = invite_member(f"ada-{uuid.uuid4().hex[:6]}@example.com", org_id)
    bo = invite_member(f"bo-{uuid.uuid4().hex[:6]}@example.com", org_id)
    linked = identities.link_github(org_id, ada.id, "ada", None)
    response = client.delete(
        f"/account/identities/{linked.id}", cookies={"session": create_session_token(bo)}
    )
    assert response.status_code == 404
    assert _rows(org_id)[("github", "ada")] == (ada.id, "oauth")
