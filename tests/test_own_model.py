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


# --- Whose key runs the checks and the company's work ---------------------------------

import app.llm.org_model as org_model_module  # noqa: E402
from app.llm import routed  # noqa: E402


@pytest.fixture
def keys(monkeypatch):
    """Who has saved a key: set per test. The routing reads these, never the picker."""
    saved = {"own": {}, "company": {}}

    def own_summary(org, user):
        model = saved["own"].get(user)
        return {"id": f"own:{model}", "model": model} if model else None

    def company_summary(org):
        model = saved["company"].get(org)
        return {"model": model} if model else None

    monkeypatch.setattr(own_model, "get_own_model_summary", own_summary)
    monkeypatch.setattr(org_model_module, "get_org_model_summary", company_summary)
    monkeypatch.setattr(own_model, "get_own_model",
                        lambda org, user: _own(saved["own"][user], f"sk-{user}") if user in saved["own"] else None)
    monkeypatch.setattr(org_model_module, "get_org_model",
                        lambda org: _own(saved["company"][org], f"sk-{org}") if org in saved["company"] else None)
    yield saved
    routed.use_model(None)
    routed._PAYER.set(None)


def test_whose_key_pays_is_the_persons_then_the_companys_then_ours(keys):
    assert routed.payer_choice("o", "u") is None                       # nobody: ours
    keys["company"]["o"] = "gpt-5"
    assert routed.payer_choice("o", "u") == "company:gpt-5"
    keys["own"]["u"] = "claude-sonnet-5-5"
    assert routed.payer_choice("o", "u") == "own:claude-sonnet-5-5"
    assert routed.payer_choice("o", None) == "company:gpt-5"           # company work: never a person's


def test_a_questions_checks_run_on_the_payers_key_not_the_pick(keys):
    keys["own"]["u"] = "gpt-5"
    provider = routed.RoutedLLMProvider(default=_Default())
    routed.use_model(HAIKU, org_id="o", user_id="u")                  # the menu says Haiku
    routed.use_payer_keys("o", "u")
    with routed.default_model_only():
        checks = provider.active()
    assert (checks.api_key, checks.billing) == ("sk-u", OWN)


def test_company_work_runs_on_the_company_key_and_is_restored(keys):
    keys["company"]["o"] = "gpt-5"
    aux = routed.PayerKeyProvider(default=_Default())
    assert isinstance(aux.active(), _Default)                          # outside any company: ours
    with routed.payer_keys("o"):
        client = aux.active()
        assert (client.api_key, client.billing) == ("sk-o", COMPANY)
    assert isinstance(aux.active(), _Default)                          # the next job starts clean

    @routed.on_company_key
    def job(adapter, org_id, *, provider):
        return aux.active()

    assert job(None, "o", provider="drive").api_key == "sk-o"
    assert isinstance(job(None, "other", provider="drive"), _Default)


def test_a_removed_key_leaves_the_checks_on_ours(keys):
    keys["company"]["o"] = "gpt-5"
    aux = routed.PayerKeyProvider(default=_Default())
    with routed.payer_keys("o"):
        del keys["company"]["o"]                                        # removed mid-job
        assert isinstance(aux.active(), _Default)


def test_a_company_model_named_like_ours_runs_on_the_companys_key(keys):
    """An admin's own Anthropic key on claude-haiku-5-5 must not answer on ours."""
    keys["company"]["o"] = HAIKU
    provider = routed.RoutedLLMProvider(default=_Default())
    routed.use_model(f"company:{HAIKU}", org_id="o")
    client = provider.active()
    assert (client.api_key, client.billing) == ("sk-o", COMPANY)
    assert billing_of(f"company:{HAIKU}") == COMPANY
    assert own_model.selectable("o", "u", f"company:{HAIKU}")


def test_the_menu_starts_on_the_persons_key_then_the_companys_then_ours(keys, monkeypatch):
    from types import SimpleNamespace

    from app.api.chat import list_models

    monkeypatch.setenv("LLM_MODEL", HAIKU)
    monkeypatch.setenv("CLAUDE_API_KEY", "c")
    session = SimpleNamespace(org_id="o", user_id="u")
    assert list_models(session=session)["preferred"] == "auto"
    keys["company"]["o"] = "gpt-5"
    menu = list_models(session=session)
    assert menu["preferred"] == "company:gpt-5" and "company:gpt-5" in [m["id"] for m in menu["models"]]
    keys["own"]["u"] = "claude-sonnet-5-5"
    assert list_models(session=session)["preferred"] == "own:claude-sonnet-5-5"


# --- The usage ring: how much of today's AI use is left ------------------------------

from app.ratelimit import Bucket, MemoryRateLimiter  # noqa: E402


def test_the_ring_shows_the_day_left_on_handbooks_key(monkeypatch, ids):
    user, org = ids
    _admin_limits(monkeypatch)
    assert rate_limit.usage(user, org, HAIKU) == {"limited": True, "left": 1.0, "whose": "person", "retry_after": 0}
    charge_to(user, org)
    charge_call("claude-sonnet-5-5", 100_000, 5_000)                 # $0.25 of the person's $0.50
    shown = rate_limit.usage(user, org, HAIKU)
    assert shown["left"] == pytest.approx(0.5, abs=0.01) and shown["whose"] == "person"
    # One budget for every model: Sonnet's use shows on Haiku too.
    assert rate_limit.usage(user, org, "claude-sonnet-5-5")["left"] == shown["left"]


def test_the_ring_names_the_company_when_its_day_runs_lowest(monkeypatch, ids):
    user, org = ids
    _admin_limits(monkeypatch, company_day=100_000)
    charge_to(str(uuid.uuid4()), org)                                # a colleague
    charge_call(HAIKU, 90_000, 0)
    shown = rate_limit.usage(user, org, HAIKU)
    assert shown["whose"] == "company" and shown["left"] == pytest.approx(0.1, abs=0.01)


def test_an_overdrawn_day_shows_empty_with_the_wait(monkeypatch, ids):
    user, org = ids
    _admin_limits(monkeypatch)
    charge_to(user, org)
    charge_call("claude-sonnet-5-5", 250_000, 2_000)                 # $0.52: past $0.50
    shown = rate_limit.usage(user, org, HAIKU)
    assert shown["left"] == 0.0 and shown["retry_after"] > 0
    assert not rate_limit.question_decision(user, org, model=HAIKU).allowed


def test_the_ring_is_hidden_on_an_own_key_without_limits(monkeypatch, ids):
    user, org = ids
    _admin_limits(monkeypatch, person_day=1, company_day=1)           # the admin's never apply to it
    assert rate_limit.usage(user, org, "own:gpt-5") == {"limited": False}
    monkeypatch.setattr(own_model, "own_limits", lambda o, u: (0, 200_000))
    assert rate_limit.usage(user, org, "own:gpt-5")["left"] == 1.0


def test_reading_levels_spends_nothing():
    clock = [0.0]
    lim = MemoryRateLimiter(clock=lambda: clock[0])
    day = Bucket("ai:user:day:u", 0.5, 0.5 / 1440)
    assert lim.levels([day]) == [0.5]                                 # unused is full
    lim.take([day], need=None, spend=0.2)
    assert lim.levels([day]) == pytest.approx([0.3])
    assert lim.levels([day]) == pytest.approx([0.3])                  # reading twice changes nothing
    clock[0] += 720 * 60                                              # half a day refills $0.25
    assert lim.levels([day]) == pytest.approx([0.5])                  # capped at full


def test_redis_levels_match_memory(redis_url):
    from app.ratelimit import RedisRateLimiter

    lim = RedisRateLimiter(redis_url)
    day = Bucket(f"ai:user:day:{uuid.uuid4()}", 0.5, 0.5 / 1440)
    assert lim.levels([day]) == [0.5]
    lim.take([day], need=None, spend=0.2)
    assert lim.levels([day]) == pytest.approx([0.3], abs=1e-4)


# --- No model connected (HANDBOOK_MODEL_ENABLED=false) -------------------------------


@pytest.fixture
def handbook_off(monkeypatch):
    monkeypatch.setenv("HANDBOOK_MODEL_ENABLED", "false")


def test_with_ours_off_and_no_key_nothing_answers_and_people_are_told(keys, handbook_off):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.core.exceptions import NoModelConfigured

    assert not routed.model_available("o", "u")
    for role, words in (("admin", "Connect your company's AI model first"),
                        ("member", "Your company hasn't connected an AI model yet")):
        with pytest.raises(HTTPException) as refused:
            rate_limit.require_model(SimpleNamespace(org_id="o", user_id="u", role=role))
        assert refused.value.status_code == 409 and refused.value.detail.startswith(words)
    provider = routed.RoutedLLMProvider(default=_Default())
    routed.use_model(None, org_id="o", user_id="u")
    routed.use_payer_keys("o", "u")
    with pytest.raises(NoModelConfigured):                             # never our key
        provider.active()
    with pytest.raises(NoModelConfigured), routed.default_model_only():
        provider.active()                                              # nor the checks
    aux = routed.PayerKeyProvider(default=_Default())
    with routed.payer_keys("o"), pytest.raises(NoModelConfigured):
        aux.active()                                                   # nor company work
    routed.use_model(HAIKU, org_id="o")
    with pytest.raises(NoModelConfigured):                             # nor our model picked by id
        provider.active()


def test_with_ours_off_a_key_answers_everything(keys, handbook_off):
    keys["company"]["o"] = "gpt-5"
    assert routed.model_available("o", "u") and routed.model_available("o")
    provider = routed.RoutedLLMProvider(default=_Default())
    routed.use_model(None, org_id="o", user_id="u")                    # "Default" = whoever's key
    routed.use_payer_keys("o", "u")
    assert provider.active().api_key == "sk-o"
    keys["own"]["u"] = "claude-sonnet-5-5"
    routed.use_payer_keys("o", "u")
    assert provider.active().api_key == "sk-u"                         # theirs first


def test_with_ours_off_the_menu_offers_only_added_keys(keys, handbook_off, monkeypatch):
    from types import SimpleNamespace

    from app.api.chat import list_models

    monkeypatch.setenv("LLM_MODEL", HAIKU)
    monkeypatch.setenv("CLAUDE_API_KEY", "c")
    session = SimpleNamespace(org_id="o", user_id="u")
    menu = list_models(session=session)
    assert menu["default_available"] is False and menu["models"] == []
    keys["company"]["o"] = "gpt-5"
    assert [m["id"] for m in list_models(session=session)["models"]] == ["company:gpt-5"]
    assert not own_model.selectable("o", "u", HAIKU)                    # ours cannot be sent
    assert own_model.selectable("o", "u", "auto")


def test_me_and_the_bell_say_whether_a_model_is_connected(keys, handbook_off):
    from app.api.notifications import _model_items
    from app.api.orgs import _ai_status

    assert _ai_status("o", "u") == {"ready": False, "handbook": False, "company": False, "own": False}
    assert [i["href"] for i in _model_items("o")] == ["/admin/model"]
    keys["own"]["u"] = "gpt-5"
    assert _ai_status("o", "u")["ready"] is True                       # they can ask on their key
    assert _model_items("o") != []                                     # the company still has none
    keys["company"]["o"] = "gpt-5"
    assert _model_items("o") == []


def test_with_ours_on_nothing_changes(keys):
    assert routed.model_available("o", "u")
    provider = routed.RoutedLLMProvider(default=_Default())
    routed.use_model(None, org_id="o", user_id="u")
    assert isinstance(provider.active(), _Default)


def test_reports_need_the_companys_model_not_a_persons(keys, handbook_off):
    """A report runs as company work: someone's own key does not run it."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    keys["own"]["u"] = "gpt-5"
    member = SimpleNamespace(org_id="o", user_id="u", role="member")
    rate_limit.require_model(member)                                   # they can ask on their key
    with pytest.raises(HTTPException) as refused:
        rate_limit.require_company_model(member)
    assert refused.value.status_code == 409 and "Reports are written with your company's" in refused.value.detail
    keys["company"]["o"] = "gpt-5"
    rate_limit.require_company_model(member)
