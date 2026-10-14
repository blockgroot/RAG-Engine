"""Act mode, Phase 0 (docs/plans/2026-10-13-write-actions.md).

Pinned here:
- the exposure verdict: private content never reads as safe for an audience
  that is public, unknown, or includes someone who cannot see every source;
- a proposal runs at most once, only for its proposer, only before it
  expires, only with the payload that was shown, and only with the person's
  acknowledgement when its exposure needs one; a public exposure is never
  stored, not even by a direct INSERT;
- a space can switch an action off but never on;
- a person's write token is theirs alone, and removing them deletes it while
  their action log survives.
"""

from __future__ import annotations

import threading
import uuid

import pytest
from cryptography.fernet import Fernet
from psycopg.types.json import Json

from app.actions import ActionTarget, Audience, Proposal, Source, assess, build_actions
from app.actions import policy, store
from app.actions.exposure import NARROWER, NONE, PUBLIC, UNKNOWN, WIDER
from app.auth import create_admin
from app.auth.user_connections import (
    UserTokens,
    connected_providers,
    delete_user_connection,
    get_user_token,
    mark_user_needs_reauth,
    save_user_connection,
)
from app.auth.users import invite_member, remove_member
from app.core.exceptions import (
    ActionConflict,
    ActionExpired,
    ActionRejected,
    ConfigurationError,
    NotFoundError,
    OAuthReauthRequiredError,
)
from app.db import get_connection

from .conftest import requires_db

HR = Source("d1", "Reorg plan", public=False, viewers=("a@x.com", "b@x.com"))
BUDGET = Source("d2", "HR budget", public=False, viewers=("A@x.com", "c@x.com"))
WIKI = Source("d3", "Wiki", public=True)


# --------------------------------------------------------------------------
# Exposure (pure)
# --------------------------------------------------------------------------


def test_public_sources_expose_nothing():
    assert assess([WIKI], Audience(public=True)).verdict == NONE
    assert assess([], Audience(members=None)).verdict == NONE


def test_private_source_to_public_destination_is_public():
    verdict = assess([HR, WIKI], Audience(public=True))
    assert verdict.verdict == PUBLIC
    assert verdict.private_titles == ("Reorg plan",)


def test_unknown_audience_is_never_treated_as_small():
    assert assess([HR], Audience(members=None)).verdict == UNKNOWN


def test_audience_inside_every_private_source_is_narrower():
    # Only a@x.com can see BOTH private documents (case-insensitively).
    verdict = assess([HR, BUDGET], Audience(members=("A@X.com",)))
    assert verdict.verdict == NARROWER
    assert verdict.outsiders == 0


def test_one_outsider_makes_it_wider_and_is_counted():
    # b@x.com can see the reorg plan but not the budget.
    verdict = assess([HR, BUDGET], Audience(members=("a@x.com", "b@x.com")))
    assert verdict.verdict == WIDER
    assert (verdict.audience_size, verdict.outsiders) == (2, 1)


def test_a_member_with_no_email_counts_as_an_outsider():
    verdict = assess([HR], Audience(members=("a@x.com", "")))
    assert verdict.verdict == WIDER
    assert verdict.outsiders == 1


def test_payload_hash_is_canonical_and_content_bound():
    target = {"kind": "linear_team", "external_id": "t", "name": "P", "url": None}
    a = store.payload_hash("x", target, {"title": "T", "body": "B"})
    b = store.payload_hash("x", dict(reversed(target.items())), {"body": "B", "title": "T"})
    assert a == b
    assert a != store.payload_hash("x", target, {"title": "T", "body": "B!"})


# --------------------------------------------------------------------------
# Store, policy, personal tokens (real database)
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


@pytest.fixture
def people(store, org_cleanup):
    """Two orgs; org A has an admin and a member."""
    org_a = store.create_organization(f"Act A {uuid.uuid4().hex[:8]}")
    org_b = store.create_organization(f"Act B {uuid.uuid4().hex[:8]}")
    org_cleanup.extend([org_a, org_b])
    admin = create_admin(f"act-admin-{uuid.uuid4().hex[:8]}@example.com", org_a)
    member = invite_member(f"act-member-{uuid.uuid4().hex[:8]}@example.com", org_a)
    outsider = create_admin(f"act-other-{uuid.uuid4().hex[:8]}@example.com", org_b)
    return org_a, org_b, admin, member, outsider


def _proposal(body: str = "Login times out after 30s") -> Proposal:
    return Proposal(
        action="linear.create_issue",
        target=ActionTarget("linear_team", "team-1", "Platform"),
        payload={"title": "Login timeout", "body": body},
    )


def _create(org_id, user_id, *, exposure=NONE, ttl=900, sources=()):
    return store.create(
        org_id=org_id, workspace_id=None, user_id=user_id, conversation_id=None,
        surface="web", proposal=_proposal(), sources=sources,
        exposure=exposure, ttl_seconds=ttl,
    )


@requires_db
def test_claim_runs_once_for_its_proposer(people):
    org_a, _org_b, _admin, member, _outsider = people
    row = _create(org_a, member.id)
    assert row.status == "proposed"

    claimed = store.claim(org_a, member.id, row.id, shown_hash=row.payload_hash)
    assert claimed.status == "executing"
    with pytest.raises(ActionConflict):
        store.claim(org_a, member.id, row.id, shown_hash=row.payload_hash)

    store.finish(org_a, row.id, status="succeeded", result={"external_id": "LIN-1"})
    store.finish(org_a, row.id, status="failed", result={"error": "late"})  # no-op
    final = store.get(org_a, member.id, row.id)
    assert final.status == "succeeded" and final.result == {"external_id": "LIN-1"}


@requires_db
def test_concurrent_approvals_have_exactly_one_winner(people):
    org_a, _org_b, _admin, member, _outsider = people
    row = _create(org_a, member.id)
    wins, losses = [], []
    barrier = threading.Barrier(5)

    def approve():
        barrier.wait()
        try:
            wins.append(store.claim(org_a, member.id, row.id, shown_hash=row.payload_hash))
        except ActionConflict:
            losses.append(1)

    threads = [threading.Thread(target=approve) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and len(losses) == 4


@requires_db
def test_nobody_else_can_see_approve_or_cancel_it(people):
    org_a, org_b, admin, member, outsider = people
    row = _create(org_a, member.id)
    # Another person in the same org -- even an admin -- and another org.
    for org, user in ((org_a, admin.id), (org_b, outsider.id), (org_b, member.id)):
        assert store.get(org, user, row.id) is None
        with pytest.raises(NotFoundError):
            store.claim(org, user, row.id, shown_hash=row.payload_hash)
        assert store.reject(org, user, row.id) is False
    assert store.get(org_a, member.id, row.id).status == "proposed"


@requires_db
def test_expired_proposal_never_runs_and_is_swept(people):
    org_a, _org_b, _admin, member, _outsider = people
    row = _create(org_a, member.id, ttl=-1)
    with pytest.raises(ActionExpired):
        store.claim(org_a, member.id, row.id, shown_hash=row.payload_hash)
    assert store.expire_stale() >= 1
    assert store.get(org_a, member.id, row.id).status == "expired"


@requires_db
def test_a_different_payload_than_shown_is_refused(people):
    org_a, _org_b, _admin, member, _outsider = people
    row = _create(org_a, member.id)
    with pytest.raises(ActionConflict, match="not what is stored"):
        store.claim(org_a, member.id, row.id, shown_hash="0" * 64)
    assert store.get(org_a, member.id, row.id).status == "proposed"


@requires_db
@pytest.mark.parametrize("exposure", [WIDER, UNKNOWN])
def test_widening_needs_the_tick(people, exposure):
    org_a, _org_b, _admin, member, _outsider = people
    row = _create(org_a, member.id, exposure=exposure, sources=[HR])
    assert row.sources == [{"document_id": "d1", "title": "Reorg plan",
                            "public": False, "viewers": 2}]
    with pytest.raises(ActionConflict, match="Tick"):
        store.claim(org_a, member.id, row.id, shown_hash=row.payload_hash)
    claimed = store.claim(org_a, member.id, row.id, shown_hash=row.payload_hash,
                          acknowledged=True)
    assert claimed.acknowledged is True


@requires_db
def test_public_exposure_is_never_stored(people):
    org_a, _org_b, _admin, member, _outsider = people
    with pytest.raises(ActionRejected):
        _create(org_a, member.id, exposure=PUBLIC, sources=[HR])
    # Not even by a caller that skips store.create.
    with pytest.raises(Exception, match="check"):
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO actions (org_id, user_id, surface, action, target, payload, "
                "payload_hash, exposure, expires_at) VALUES (%s, %s, 'web', 'x', %s, %s, "
                "'h', 'public', now() + interval '1 minute')",
                (org_a, member.id, Json({}), Json({})),
            )


@requires_db
def test_reject_only_cancels_a_pending_proposal(people):
    org_a, _org_b, _admin, member, _outsider = people
    row = _create(org_a, member.id)
    assert store.reject(org_a, member.id, row.id) is True
    assert store.reject(org_a, member.id, row.id) is False
    with pytest.raises(ActionConflict):
        store.claim(org_a, member.id, row.id, shown_hash=row.payload_hash)


@requires_db
def test_a_space_can_switch_an_action_off_but_never_on(people, monkeypatch):
    org_a, _org_b, admin, member, _outsider = people
    ws = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO workspaces (id, org_id, name, created_by) VALUES (%s, %s, 'Space', %s)",
            (ws, org_a, member.id),
        )
    assert policy.enabled_actions(org_a, None) == set()

    policy.set_policy(org_a, "linear.comment", True, workspace_id=ws)  # a space alone
    assert "linear.comment" not in policy.enabled_actions(org_a, ws)

    policy.set_policy(org_a, "linear.create_issue", True, updated_by=admin.id)
    policy.set_policy(org_a, "linear.create_issue", False, workspace_id=ws)
    assert "linear.create_issue" in policy.enabled_actions(org_a, None)
    assert "linear.create_issue" not in policy.enabled_actions(org_a, ws)

    # Every action waits on the company's "Allow Act" switch.
    assert not policy.is_enabled(org_a, None, "linear.create_issue")
    policy.set_policy(org_a, policy.ACT_KEY, True, updated_by=admin.id)
    assert policy.is_enabled(org_a, None, "linear.create_issue")
    assert not policy.is_enabled(org_a, ws, "linear.create_issue")


@requires_db
def test_rate_cap_counts_sent_writes(people):
    from app.config.settings import ActionsSettings

    org_a, _org_b, _admin, member, _outsider = people
    two = ActionsSettings(rate_per_hour=2)
    for _ in range(2):
        assert policy.within_rate(org_a, member.id, settings=two)
        row = _create(org_a, member.id)
        store.claim(org_a, member.id, row.id, shown_hash=row.payload_hash)
    _create(org_a, member.id)  # proposed, not sent: does not count
    assert not policy.within_rate(org_a, member.id, settings=two)


@requires_db
def test_personal_tokens_are_the_owners_alone(people):
    org_a, org_b, admin, member, outsider = people
    save_user_connection(org_a, member.id, "linear", UserTokens(
        access_token="lin_member_SECRET", external_user_id="u-1",
        refresh_token="r-1", scopes=("read", "issues:create")))

    token = get_user_token(org_a, member.id, "linear")
    assert token.access_token == "lin_member_SECRET"
    assert token.scopes == ("read", "issues:create")
    with get_connection() as conn:
        raw = conn.execute(
            "SELECT access_token_encrypted FROM user_connections WHERE user_id = %s",
            (member.id,),
        ).fetchone()[0]
    assert "SECRET" not in raw

    for org, user in ((org_a, admin.id), (org_b, member.id), (org_b, outsider.id)):
        with pytest.raises(ConfigurationError):
            get_user_token(org, user, "linear")
    assert connected_providers(org_a, member.id) == {"linear"}

    mark_user_needs_reauth(org_a, member.id, "linear")
    with pytest.raises(OAuthReauthRequiredError):
        get_user_token(org_a, member.id, "linear")
    assert connected_providers(org_a, member.id) == set()

    assert delete_user_connection(org_a, member.id, "linear") is True
    assert delete_user_connection(org_a, member.id, "linear") is False


@requires_db
def test_removing_a_person_drops_their_token_and_keeps_the_log(people):
    org_a, _org_b, admin, member, _outsider = people
    save_user_connection(org_a, member.id, "github", UserTokens(
        access_token="ghu_x", external_user_id="octo"))
    row = _create(org_a, member.id)

    remove_member(member.id, org_a, acting_user_id=admin.id)

    with get_connection() as conn:
        tokens = conn.execute(
            "SELECT count(*) FROM user_connections WHERE user_id = %s", (member.id,)
        ).fetchone()[0]
    assert tokens == 0
    logged = store.list_for_org(org_a)
    assert [r.id for r in logged] == [row.id] and logged[0].user_id is None


@requires_db
def test_nothing_is_offered_while_act_is_off(people, monkeypatch):
    org_a, _org_b, _admin, member, _outsider = people
    policy.set_policy(org_a, "linear.create_issue", True)  # but Act never allowed
    assert build_actions(org_a, None, member.id) == []
