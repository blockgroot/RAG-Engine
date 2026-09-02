"""``/insights`` — the routes behind the Visualizations section.

The valuable tests here are the refusals. A chart that renders with another
tenant's counts in it looks completely normal, so scoping is asserted from the
outside (through the HTTP layer, with a real session cookie) rather than only
at the store.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth import OAuthTokens, create_admin, create_session_token, save_connection
from app.auth.users import invite_member
from app.db import get_connection

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("EMAIL_SENDER", "console")


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


def _org(store, org_cleanup, provider="notion"):
    org_id = store.create_organization(f"Insights Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    create_admin(f"admin-{uuid.uuid4().hex[:8]}@example.com", org_id)
    member = invite_member(f"member-{uuid.uuid4().hex[:8]}@example.com", org_id)
    save_connection(
        org_id, provider,
        OAuthTokens(access_token="fake", refresh_token=None, expires_at=None,
                    external_workspace_id=f"ws-{uuid.uuid4().hex[:6]}"),
    )
    return org_id, member, {"session": create_session_token(member)}


@pytest.fixture
def org(store, org_cleanup):
    return _org(store, org_cleanup)


def _fact(org_id, *, workspace_id=None, provider="notion", actor=None, days_ago=1):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO activity_facts
                (org_id, workspace_id, provider, kind, actor, subject,
                 occurred_at, external_id)
            VALUES (%s, %s, %s, 'doc_changed', %s, 'A page', %s, %s)
            """,
            (org_id, workspace_id, provider, actor,
             datetime.now(timezone.utc) - timedelta(days=days_ago),
             uuid.uuid4().hex),
        )
        conn.commit()


# --------------------------------------------------------------------------
# Auth + scoping — the refusals
# --------------------------------------------------------------------------


@requires_db
def test_every_route_requires_a_session(client):
    assert client.get("/insights/scopes").status_code == 401
    assert client.get("/insights/dashboard").status_code == 401
    assert client.get("/insights/freshness").status_code == 401


@requires_db
def test_a_dashboard_counts_only_the_callers_org(client, org, store, org_cleanup):
    """The bug this guards against renders perfectly: a chart with someone
    else's numbers looks exactly like a chart with yours."""
    org_id, _, cookies = org
    other_id, _, _ = _org(store, org_cleanup)

    _fact(org_id)
    _fact(other_id)
    _fact(other_id)

    body = client.get("/insights/dashboard", cookies=cookies).json()
    line = next(p for p in body["panels"] if p["group_by"] is None)
    assert sum(pt["value"] for pt in line["points"]) == 1


@requires_db
def test_a_space_the_member_is_not_in_is_refused_not_emptied(
    client, org, store, org_cleanup
):
    """403, never an empty dashboard: empty reads as "nothing happened in that
    space", which is a claim about content the caller is not allowed to have."""
    org_id, _, _ = org
    outsider = invite_member(f"outsider-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner = invite_member(f"owner-{uuid.uuid4().hex[:8]}@example.com", org_id)

    from app.workspaces.store import create_workspace

    space = create_workspace(org_id, "Private notes", owner.id)
    cookies = {"session": create_session_token(outsider)}

    assert client.get(f"/insights/dashboard?scope={space}", cookies=cookies).status_code == 403
    assert client.get(f"/insights/freshness?scope={space}", cookies=cookies).status_code == 403


@requires_db
def test_another_orgs_space_id_is_refused(client, org, store, org_cleanup):
    """A workspace id is guessable in a way an org id is not, so the membership
    check has to be the thing that stops it -- not the org filter."""
    _, _, cookies = org
    other_id, other_member, _ = _org(store, org_cleanup)

    from app.workspaces.store import create_workspace

    foreign = create_workspace(other_id, "Their space", other_member.id)

    assert client.get(f"/insights/dashboard?scope={foreign}", cookies=cookies).status_code == 403


@requires_db
def test_a_space_scope_excludes_org_wide_rows(client, org):
    """A space sees ONLY its own rows. A meeting-notes space whose charts
    include company-wide content makes membership meaningless."""
    org_id, member, cookies = org

    from app.workspaces.store import create_workspace

    space = create_workspace(org_id, "Meeting notes", member.id)
    save_connection(
        org_id, "notion",
        OAuthTokens(access_token="f", refresh_token=None, expires_at=None,
                    external_workspace_id=f"sp-{uuid.uuid4().hex[:6]}"),
        workspace_id=space,
    )
    _fact(org_id, workspace_id=None)
    _fact(org_id, workspace_id=space)

    body = client.get(f"/insights/dashboard?scope={space}", cookies=cookies).json()
    line = next(p for p in body["panels"] if p["group_by"] is None)
    assert sum(pt["value"] for pt in line["points"]) == 1


# --------------------------------------------------------------------------
# Shape of the answer
# --------------------------------------------------------------------------


@requires_db
def test_an_unknown_period_is_refused(client, org):
    _, _, cookies = org
    assert client.get("/insights/dashboard?period=fortnight", cookies=cookies).status_code == 400


@requires_db
def test_the_company_scope_is_offered_even_with_nothing_connected(
    client, store, org_cleanup
):
    org_id = store.create_organization(f"Bare Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    member = invite_member(f"m-{uuid.uuid4().hex[:8]}@example.com", org_id)
    cookies = {"session": create_session_token(member)}

    body = client.get("/insights/scopes", cookies=cookies).json()
    assert [s["id"] for s in body["scopes"]] == [None]
    assert body["scopes"][0]["providers"] == []
    assert body["scopes"][0]["chartable"] == []


@requires_db
def test_a_connected_provider_with_no_panels_is_reported_separately(
    client, store, org_cleanup, monkeypatch
):
    """"Connected but no charts yet" must not look like an empty dashboard --
    the two need different copy, so they are different fields.

    Every shipped provider now HAS panels, so the mechanism is exercised by
    removing them rather than by naming a provider that happens to lack them.
    Forms will be exactly this case when its connector lands before its
    charts."""
    from app.insights import panels

    org_id, _, cookies = _org(store, org_cleanup, provider="slack")
    monkeypatch.setitem(panels.PANELS, "slack", ())

    body = client.get("/insights/scopes", cookies=cookies).json()
    scope = body["scopes"][0]
    assert scope["providers"] == ["slack"]
    assert scope["chartable"] == []


@requires_db
def test_every_shipped_provider_is_chartable(client, store, org_cleanup):
    """The mirror: a connector with charts must report itself as chartable, or
    the page hides working charts behind a "nothing built yet" message."""
    from app.insights import panels

    for provider in ("notion", "google", "slack", "linear", "github"):
        assert panels.for_provider(provider), f"{provider} has no panels"


@requires_db
def test_a_panel_with_no_data_returns_an_empty_list_not_null(client, org):
    """[] means "it ran, nothing to show"; null means "it failed". The frontend
    says different things, so the API must not conflate them."""
    _, _, cookies = org
    body = client.get("/insights/dashboard", cookies=cookies).json()
    assert body["panels"], "a connected provider must still yield panels"
    assert all(p["points"] == [] for p in body["panels"])


@requires_db
def test_a_grouped_panel_splits_by_actor(client, org):
    _, _, cookies = org
    _fact(org[0], actor="ada")
    _fact(org[0], actor="ada")
    _fact(org[0], actor="grace")

    body = client.get("/insights/dashboard", cookies=cookies).json()
    editors = next(p for p in body["panels"] if p["group_by"] == "actor")
    totals = {}
    for point in editors["points"]:
        totals[point["group"]] = totals.get(point["group"], 0) + point["value"]
    assert totals == {"ada": 2, "grace": 1}


@requires_db
def test_a_panel_reports_when_measurement_began(client, org):
    """Without this the axis silently starts on deploy day and reads as if
    nobody worked before it. Author names cannot be backfilled at all."""
    _, _, cookies = org
    _fact(org[0], days_ago=5)

    body = client.get("/insights/dashboard", cookies=cookies).json()
    assert all(p["measured_since"] for p in body["panels"])


@requires_db
def test_freshness_reports_a_dead_token_rather_than_only_an_old_date(client, org):
    """Auto-sync skips a `needs_reauth` row entirely, so waiting can never make
    it current. Reporting only the date invites someone to wait."""
    org_id, _, cookies = org
    with get_connection() as conn:
        conn.execute(
            "UPDATE oauth_connections SET needs_reauth = true, last_sync_at = now() "
            "WHERE org_id = %s",
            (org_id,),
        )
        conn.commit()

    body = client.get("/insights/freshness", cookies=cookies).json()
    assert body["connectors"][0]["needs_reauth"] is True


# --------------------------------------------------------------------------
# The ask box and pins
# --------------------------------------------------------------------------


@requires_db
def test_ask_requires_a_session(client):
    assert client.post("/insights/ask", json={"question": "x"}).status_code == 401
    assert client.get("/insights/pins").status_code == 401


@requires_db
def test_ask_returns_a_chart_for_a_resolvable_question(client, org, monkeypatch):
    """The model only ever selects a registry key, so this stubs the selection
    and asserts the real SQL path ran."""
    from app.insights import resolve

    _, _, cookies = org
    _fact(org[0], actor="ada")
    monkeypatch.setattr(
        resolve, "resolve_question",
        lambda q, *, providers, llm=None: resolve.ChartSpec(
            metric="docs_changed", group_by="actor", period="month", chart="bar"
        ),
    )

    body = client.post("/insights/ask", json={"question": "top editors"},
                       cookies=cookies).json()
    assert body["charted"] is True
    assert body["panel"]["group_by"] == "actor"
    assert body["panel"]["points"][0]["group"] == "ada"


@requires_db
def test_an_unchartable_question_answers_with_guidance_not_an_error(
    client, org, monkeypatch
):
    """200 with `charted: false`. "I can't chart that, here is what I can" is
    an ANSWER -- rendering it as an error banner would read as a fault."""
    from app.insights import resolve

    _, _, cookies = org
    monkeypatch.setattr(
        resolve, "resolve_question",
        lambda q, *, providers, llm=None: (_ for _ in ()).throw(
            resolve.CannotChart("I can't chart that. What I can show: pages.")
        ),
    )

    response = client.post("/insights/ask", json={"question": "team morale"},
                           cookies=cookies)
    assert response.status_code == 200
    body = response.json()
    assert body["charted"] is False
    assert "pages" in body["message"], "a refusal must say what IS available"


@requires_db
def test_ask_on_a_space_the_member_is_not_in_is_refused(client, org):
    org_id, _, _ = org
    outsider = invite_member(f"out-{uuid.uuid4().hex[:8]}@example.com", org_id)
    owner = invite_member(f"own-{uuid.uuid4().hex[:8]}@example.com", org_id)

    from app.workspaces.store import create_workspace

    space = create_workspace(org_id, "Private", owner.id)
    cookies = {"session": create_session_token(outsider)}

    response = client.post(
        "/insights/ask", json={"question": "anything", "scope": space}, cookies=cookies
    )
    assert response.status_code == 403


@requires_db
def test_an_over_long_question_is_rejected_by_validation(client, org):
    _, _, cookies = org
    response = client.post("/insights/ask", json={"question": "x" * 5000},
                           cookies=cookies)
    assert response.status_code == 422


@requires_db
def test_a_pin_round_trips_and_is_personal(client, org):
    """`(org_id, user_id)` scoping, like a scheduler: a pin is one person's
    shortcut and is never visible to anyone else."""
    org_id, _, cookies = org
    other = invite_member(f"other-{uuid.uuid4().hex[:8]}@example.com", org_id)
    other_cookies = {"session": create_session_token(other)}

    created = client.post(
        "/insights/pins",
        json={"metric": "docs_changed", "group_by": "actor", "period": "month"},
        cookies=cookies,
    )
    assert created.status_code == 201

    mine = client.get("/insights/pins", cookies=cookies).json()["pins"]
    theirs = client.get("/insights/pins", cookies=other_cookies).json()["pins"]
    assert len(mine) == 1
    assert theirs == [], "a pin must never leak to another member of the same org"


@requires_db
def test_pinning_the_same_chart_twice_is_not_a_duplicate(client, org):
    _, _, cookies = org
    payload = {"metric": "docs_changed", "group_by": "actor", "period": "month"}
    client.post("/insights/pins", json=payload, cookies=cookies)
    client.post("/insights/pins", json=payload, cookies=cookies)

    assert len(client.get("/insights/pins", cookies=cookies).json()["pins"]) == 1


@requires_db
def test_a_pin_is_validated_against_the_registry_not_trusted(client, org):
    """A pin is re-run on every page load, so an unvalidated one would be a
    STORED request to run_metric with caller-controlled identifiers -- the one
    place in this feature where a bad value would persist rather than fail
    once."""
    _, _, cookies = org

    assert client.post("/insights/pins", json={
        "metric": "'; DROP TABLE activity_facts; --", "period": "month",
    }, cookies=cookies).status_code == 400

    assert client.post("/insights/pins", json={
        "metric": "docs_changed", "period": "fortnight",
    }, cookies=cookies).status_code == 400

    assert client.post("/insights/pins", json={
        "metric": "docs_changed", "group_by": "provider", "period": "month",
    }, cookies=cookies).status_code == 400


@requires_db
def test_deleting_someone_elses_pin_is_a_404(client, org):
    org_id, _, cookies = org
    other = invite_member(f"o-{uuid.uuid4().hex[:8]}@example.com", org_id)
    other_cookies = {"session": create_session_token(other)}

    pin_id = client.post(
        "/insights/pins", json={"metric": "docs_changed", "period": "month"},
        cookies=cookies,
    ).json()["id"]

    assert client.delete(f"/insights/pins/{pin_id}",
                         cookies=other_cookies).status_code == 404
    assert client.delete(f"/insights/pins/{pin_id}",
                         cookies=cookies).status_code == 204
