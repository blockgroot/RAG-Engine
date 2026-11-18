"""Bring your own model, and the limits an admin sets.

Whose key answers decides which limits apply: ours (Handbook's, narrowed by
the admin's), the company's (the admin's only), a person's own (none). The
failures that matter are silent: a person's key spent on another person's
question, our credit charged for a call the company paid for, or an admin
raising what we pay for.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.exceptions import ConfigurationError
from app.llm import own_model
from app.llm.org_model import OrgModel
from app.llm.own_model import COMPANY, OURS, OWN, billing_of
from app.security import ai_spend, rate_limit
from app.security.ai_spend import charge_call, charge_to

HAIKU = "claude-haiku-5-5"
#: The real reader, kept before the autouse fixture stubs it for the limiter tests.
_read_own_limits = own_model.own_limits


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    charge_to(None, None)
    monkeypatch.setattr(rate_limit, "_member_count", lambda org: 30)
    monkeypatch.setattr(own_model, "own_limits", lambda org, user: (0, 0))   # none set
    yield
    charge_to(None, None)


@pytest.fixture
def ids():
    return str(uuid.uuid4()), str(uuid.uuid4())


def _admin_limits(monkeypatch, person_min=0, person_day=0, company_min=0, company_day=0):
    limits = (person_min, person_day, company_min, company_day)
    monkeypatch.setattr(ai_spend, "org_limits", lambda org: limits)


@pytest.mark.parametrize("model, billing", [
    (None, OURS), ("", OURS), ("auto", OURS), (HAIKU, OURS),
    ("own:gpt-5", OWN), ("gpt-5", COMPANY),
])
def test_whose_key_answers_is_read_from_the_pick(model, billing):
    assert billing_of(model) == billing


# --- Limits by whose key answers ----------------------------------------------


def test_an_admin_limit_narrows_ours(monkeypatch, ids):
    user, org = ids
    _admin_limits(monkeypatch, person_min=1_000)
    charge_to(user, org)
    charge_call(HAIKU, 900, 200)                         # 1,100 tokens: past the admin's 1,000
    decision = rate_limit.question_decision(user, org, model=HAIKU)
    assert not decision.allowed and decision.refused_by == f"ai:user:amin:{user}"
    assert rate_limit.refusal_message(decision.retry_after, decision.refused_by).startswith(
        "You've reached the AI usage limit your company set.")


def test_an_admin_cannot_raise_ours(monkeypatch, ids):
    """A limit far above ours changes nothing: ours still refuses."""
    user, org = ids
    _admin_limits(monkeypatch, person_min=10_000_000)
    charge_to(user, org)
    charge_call(HAIKU, 61_000, 0)                        # past our 60k input a minute
    decision = rate_limit.question_decision(user, org, model=HAIKU)
    assert not decision.allowed and decision.refused_by == f"ai:user:tin:{HAIKU}:{user}"


def test_the_company_key_is_never_charged_to_our_credit(monkeypatch, ids):
    user, org = ids
    _admin_limits(monkeypatch)
    charge_to(user, org)
    # A company model with a priced id: still the company's bill, not ours.
    assert charge_call(HAIKU, 10_000_000, 1_000_000, billing=COMPANY) == 0.0
    assert rate_limit.question_decision(user, org, model=HAIKU).allowed


def test_the_company_key_takes_the_admins_day_limit(monkeypatch, ids):
    user, org = ids
    _admin_limits(monkeypatch, company_day=50_000)
    charge_to(user, org)
    charge_call("gpt-5", 40_000, 20_000, billing=COMPANY)
    decision = rate_limit.question_decision(user, org, model="gpt-5")
    assert not decision.allowed and decision.refused_by == f"ai:org:aday:{org}"
    # Wait for a day's limit is said in hours or minutes.
    assert rate_limit.refusal_message(decision.retry_after, decision.refused_by).endswith(
        ("minutes.", "hour.", "hours."))


def test_a_persons_own_key_takes_none_of_our_or_the_admins_limits(monkeypatch, ids):
    user, org = ids
    _admin_limits(monkeypatch, person_min=1, person_day=1, company_min=1, company_day=1)
    charge_to(user, org)
    assert charge_call("gpt-5", 10_000_000, 1_000_000, billing=OWN) == 0.0
    assert rate_limit.question_decision(user, org, model="own:gpt-5").allowed
    # Their spending never touched the company's: a colleague on ours is fine.
    assert rate_limit.question_decision(str(uuid.uuid4()), org, model=HAIKU).allowed


def test_a_person_may_limit_their_own_key(monkeypatch, ids):
    user, org = ids
    _admin_limits(monkeypatch)
    monkeypatch.setattr(own_model, "own_limits", lambda o, u: (0, 30_000))
    charge_to(user, org)
    charge_call("gpt-5", 25_000, 6_000, billing=OWN)     # 31,000: past their 30,000 a day
    decision = rate_limit.question_decision(user, org, model="own:gpt-5")
    assert not decision.allowed and decision.refused_by == f"ai:user:oday:{user}"
    assert rate_limit.refusal_message(decision.retry_after, decision.refused_by).startswith(
        "You've reached the limit you set for your own model.")
    # Their own cap never touches Handbook's model.
    assert rate_limit.question_decision(user, org, model=HAIKU).allowed


def test_no_admin_limits_is_handbooks_limits_alone(monkeypatch, ids):
    user, org = ids
    _admin_limits(monkeypatch)
    assert ai_spend.admin_buckets(user, org) == []


# --- Choosing a person's own model -----------------------------------------------


def _own(model, key, version="1"):
    return OrgModel(model=model, api_key=key, base_url="https://api.openai.com/v1",
                    preset="openai", label=model, version=version)


class _Default:
    model = "default"


def test_each_persons_own_model_uses_their_own_key(monkeypatch):
    from app.llm.routed import RoutedLLMProvider, use_model

    keys = {"u-a": "sk-AAAA", "u-b": "sk-BBBB"}
    monkeypatch.setattr(own_model, "get_own_model", lambda org, user: _own("gpt-5", keys[user]))
    routed = RoutedLLMProvider(default=_Default())
    try:
        use_model("own:gpt-5", org_id="o", user_id="u-a")
        a = routed.active()
        use_model("own:gpt-5", org_id="o", user_id="u-b")
        b = routed.active()
    finally:
        use_model(None)
    assert (a.api_key, b.api_key) == ("sk-AAAA", "sk-BBBB")
    assert a.billing == b.billing == OWN


def test_an_own_model_that_is_not_set_up_fails_closed(monkeypatch):
    """Never answered on our key or the company's instead."""
    from app.llm.routed import RoutedLLMProvider, use_model

    routed = RoutedLLMProvider(default=_Default())
    try:
        monkeypatch.setattr(own_model, "get_own_model", lambda org, user: None)
        use_model("own:gpt-5", org_id="o", user_id="u")
        with pytest.raises(ConfigurationError):
            routed.active()
        monkeypatch.setattr(own_model, "get_own_model", lambda org, user: _own("gpt-4", "sk"))
        with pytest.raises(ConfigurationError):            # theirs is another model
            routed.active()
        use_model("own:gpt-5", org_id="o")                 # nobody asking
        with pytest.raises(ConfigurationError):
            routed.active()
    finally:
        use_model(None)


def test_only_the_person_may_pick_their_own_model(monkeypatch):
    mine = {"u-a": {"id": "own:gpt-5"}}
    monkeypatch.setattr(own_model, "get_own_model_summary", lambda org, user: mine.get(user))
    assert own_model.selectable("o", "u-a", "own:gpt-5")
    assert not own_model.selectable("o", "u-b", "own:gpt-5")
    assert not own_model.selectable("o", None, "own:gpt-5")
    assert not own_model.selectable("o", "u-a", "own:gpt-4")


def test_the_company_client_is_billed_to_the_company(monkeypatch):
    import app.llm.org_model as om
    from app.llm.routed import RoutedLLMProvider, use_model

    monkeypatch.setattr(om, "get_org_model", lambda org: _own("gpt-5", "sk-C"))
    routed = RoutedLLMProvider(default=_Default())
    try:
        use_model("gpt-5", org_id="o")
        assert routed.active().billing == COMPANY
    finally:
        use_model(None)


# --- The admin's limits form ---------------------------------------------------------


@pytest.mark.parametrize("bad", [-1, 1.5, "100", True, 2_000_000_000])
def test_a_limit_must_be_a_whole_number_in_range(bad):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.api.llm_model import put_limits

    with pytest.raises(HTTPException) as err:
        put_limits({"person_tokens_per_day": bad}, session=SimpleNamespace(org_id="o"))
    assert err.value.status_code == 400


# --- Storage (staging database) ----------------------------------------------------

from tests.conftest import requires_db  # noqa: E402


@pytest.fixture
def person():
    from app.db import apply_schema, get_connection

    apply_schema()
    with get_connection() as conn:
        org = str(conn.execute("INSERT INTO organizations (name) VALUES (%s) RETURNING id",
                               (f"own-model-{uuid.uuid4().hex[:8]}",)).fetchone()[0])
        user = str(conn.execute("INSERT INTO users (email, org_id, role) VALUES (%s, %s, 'member') "
                                "RETURNING id", (f"{uuid.uuid4().hex[:8]}@example.com", org)).fetchone()[0])
    yield org, user
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s", (org,))


@requires_db
def test_an_own_key_is_stored_encrypted_and_never_shown(person):
    from app.db import get_connection

    org, user = person
    own_model.save_own_model(org, user, "openai", "gpt-5", "sk-secret-1234", "native")
    assert own_model.get_own_model_summary(org, user)["tokens_per_day"] == 0     # none by default
    assert own_model.save_own_limits(org, user, 10_000, 200_000)
    assert _read_own_limits(org, user) == (10_000, 200_000)
    with get_connection() as conn:
        stored = conn.execute("SELECT api_key_encrypted FROM user_llm_models WHERE user_id = %s",
                              (user,)).fetchone()[0]
    assert "sk-secret" not in stored
    summary = own_model.get_own_model_summary(org, user)
    assert summary["id"] == "own:gpt-5" and summary["key_tail"] == "1234"
    assert "sk-secret-1234" not in repr(summary)
    assert own_model.get_own_model(org, user).api_key == "sk-secret-1234"
    # Another person in the company has none.
    assert own_model.get_own_model_summary(org, str(uuid.uuid4())) is None
    own_model.delete_own_model(org, user)
    own_model.delete_own_model(org, user)                 # idempotent
    assert own_model.get_own_model(org, user) is None
    assert not own_model.save_own_limits(org, user, 1, 1)   # nothing to limit


@requires_db
def test_the_admins_limits_are_saved_and_read_back(person):
    from types import SimpleNamespace

    from app.api.llm_model import get_limits, put_limits

    org, _ = person
    session = SimpleNamespace(org_id=org)
    # Handbook's defaults until the admin sets their own.
    shown = get_limits(session=session)
    assert shown["limits"] == shown["defaults"]
    assert shown["limits"]["person_tokens_per_minute"] == 68_000
    saved = put_limits({"person_tokens_per_day": 200_000, "company_tokens_per_minute": 50_000},
                       session=session)["limits"]
    assert saved == {"person_tokens_per_minute": 0, "person_tokens_per_day": 200_000,
                     "company_tokens_per_minute": 50_000, "company_tokens_per_day": 0}


@pytest.mark.parametrize("raw, expected", [
    (None, (68_000, 4_000_000, 440_000, 30_000_000)),
    ("1:2:3:4", (1, 2, 3, 4)),
    ("0:0:0:0", (0, 0, 0, 0)),
    ("1:2:3", (68_000, 4_000_000, 440_000, 30_000_000)),      # malformed: defaults kept
    ("1:-2:3:4", (68_000, 4_000_000, 440_000, 30_000_000)),
])
def test_default_company_limits_come_from_settings(monkeypatch, raw, expected):
    from app.config.settings import RateLimitSettings

    if raw is None:
        monkeypatch.delenv("AI_ORG_DEFAULT_LIMITS", raising=False)
    else:
        monkeypatch.setenv("AI_ORG_DEFAULT_LIMITS", raw)
    assert RateLimitSettings.from_env().org_default_limits == expected
