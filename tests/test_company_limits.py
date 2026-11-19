"""The company's own key (Admin > Model): only its admin's token limits apply,
never Handbook's dollar budget; Handbook's model keeps Handbook's limits."""

from __future__ import annotations

import uuid

import pytest

from app.security import ai_spend, rate_limit
from app.security.ai_spend import COMPANY, OURS, billing_of, charge_call, charge_to

COMPANY_MODEL = "gpt-5-company"   # not in our catalog: the company's own


@pytest.fixture
def ids(monkeypatch):
    monkeypatch.setattr(rate_limit, "_member_count", lambda org: 30)
    charge_to(None, None)
    yield str(uuid.uuid4()), str(uuid.uuid4())
    charge_to(None, None)


def _limits(monkeypatch, person_min=0, person_day=0, company_min=0, company_day=0):
    monkeypatch.setattr(ai_spend, "org_limits", lambda org: (person_min, person_day, company_min, company_day))


def test_whose_key_answers():
    assert billing_of(None) == OURS and billing_of("auto") == OURS
    assert billing_of("claude-haiku-5-5") == OURS
    assert billing_of(COMPANY_MODEL) == COMPANY


def test_the_company_key_never_spends_handbooks_budget(ids, monkeypatch):
    _limits(monkeypatch)
    user, org = ids
    charge_to(user, org)
    # Priced like Sonnet if it were ours; on their key it costs us nothing.
    assert charge_call("claude-sonnet-5-5", 250_000, 2_000, billing=COMPANY) == 0.0
    assert rate_limit.question_decision(user, org).allowed           # Haiku day untouched
    assert rate_limit.question_decision(user, org, model=COMPANY_MODEL).allowed  # no admin limit


def test_handbooks_empty_day_does_not_stop_the_company_key(ids, monkeypatch):
    _limits(monkeypatch)
    user, org = ids
    charge_to(user, org)
    charge_call("claude-haiku-5-5", 5_000_000, 100_000)               # far past $0.50
    assert not rate_limit.question_decision(user, org).allowed
    assert rate_limit.question_decision(user, org, model=COMPANY_MODEL).allowed


def test_the_admins_person_day_stops_that_person_on_the_company_key(ids, monkeypatch):
    _limits(monkeypatch, person_day=50_000)
    user, org = ids
    charge_to(user, org)
    charge_call(COMPANY_MODEL, 48_000, 4_000, billing=COMPANY)
    decision = rate_limit.question_decision(user, org, model=COMPANY_MODEL)
    assert not decision.allowed and decision.refused_by == f"ai:user:aday:{user}"
    message = rate_limit.refusal_message(decision.retry_after, decision.refused_by)
    assert message.startswith("You've reached the AI usage limit your company set.")
    # A colleague, and Handbook's own model, are unaffected.
    assert rate_limit.question_decision(str(uuid.uuid4()), org, model=COMPANY_MODEL).allowed
    assert rate_limit.question_decision(user, org).allowed


def test_the_admins_company_minute_stops_everyone_on_the_key(ids, monkeypatch):
    _limits(monkeypatch, company_min=30_000)
    user, org = ids
    for _ in range(2):
        charge_to(str(uuid.uuid4()), org)
        charge_call(COMPANY_MODEL, 15_000, 1_000, billing=COMPANY)
    decision = rate_limit.question_decision(user, org, model=COMPANY_MODEL)
    assert not decision.allowed and decision.refused_by == f"ai:org:amin:{org}"


def test_the_ring_on_the_company_key_shows_the_admins_day(ids, monkeypatch):
    user, org = ids
    _limits(monkeypatch)
    assert rate_limit.usage(user, org, COMPANY_MODEL) == {"limited": False}
    _limits(monkeypatch, person_min=1_000_000, person_day=100_000)
    charge_to(user, org)
    charge_call(COMPANY_MODEL, 20_000, 5_000, billing=COMPANY)
    ring = rate_limit.usage(user, org, COMPANY_MODEL)
    assert ring["limited"] and ring["whose"] == "person"
    assert ring["left"] == pytest.approx(0.75, abs=0.01)


def test_limits_api_checks_every_value(monkeypatch):
    from fastapi import HTTPException

    from app.api import llm_model

    session = type("S", (), {"org_id": str(uuid.uuid4())})()
    for bad in (-1, 1.5, "100", True, llm_model.MAX_LIMIT_TOKENS + 1):
        with pytest.raises(HTTPException) as err:
            llm_model.put_limits({"person_tokens_per_day": bad}, session)
        assert err.value.status_code == 400


def test_limits_save_and_read_back_on_the_real_table():
    from app.api import llm_model
    from app.db.connection import get_connection

    with get_connection() as conn:
        org = conn.execute("SELECT id::text FROM organizations LIMIT 1").fetchone()
    if not org:
        pytest.skip("no organization in the test database")
    session = type("S", (), {"org_id": org[0]})()
    before = llm_model.get_limits(session)["limits"]
    try:
        out = llm_model.put_limits({"person_tokens_per_day": 200_000, "company_tokens_per_minute": 90_000}, session)
        assert out["limits"] == {"person_tokens_per_minute": 0, "person_tokens_per_day": 200_000,
                                 "company_tokens_per_minute": 90_000, "company_tokens_per_day": 0}
    finally:
        llm_model.put_limits(before, session)
