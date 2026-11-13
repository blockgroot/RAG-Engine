"""AI spend limits: real cost charged per call, a question refused once a bucket is empty.

Memory limiter by default (conftest clears REDIS_URL); the last test runs the
same charge and check in a real Redis.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.config.settings import RateLimitSettings
from app.llm.openai_provider import OpenAICompatProvider
from app.ratelimit import Bucket, MemoryRateLimiter, RedisRateLimiter
from app.security import ai_spend, rate_limit
from app.security.ai_spend import charge_call, charge_to, cost_of, price_of

S = RateLimitSettings.from_env()


@pytest.fixture(autouse=True)
def _nobody_paying():
    charge_to(None, None)
    yield
    charge_to(None, None)


@pytest.fixture
def ids(monkeypatch):
    monkeypatch.setattr(rate_limit, "_member_count", lambda org: 30)
    return str(uuid.uuid4()), str(uuid.uuid4())


@pytest.mark.parametrize("model, price", [
    ("claude-sonnet-5-5", (2.0, 10.0)),
    ("claude-haiku-5-5", (0.10, 0.50)),
    ("claude-haiku-5-5-20261001", (0.10, 0.50)),    # dated id
    ("anthropic/claude-sonnet-5.5", (2.0, 10.0)),   # vendor prefix, dotted version
    ("CLAUDE-HAIKU-5-5", (0.10, 0.50)),
])
def test_each_answering_model_has_its_price(model, price):
    assert price_of(model, S) == price


@pytest.mark.parametrize("model", ["gemini-3.1-flash-lite", "gpt-oss:120b", "claude-sonnet-55", "claude-sonnet-5", "", None])
def test_a_model_not_paid_from_the_credit_costs_nothing(model):
    assert price_of(model, S) is None
    assert cost_of(model, 10_000, 1_000, S) == 0.0


def test_a_typical_question_costs_what_the_benchmarks_say():
    # Claude benchmark median: ~5.7k in, ~0.4k out.
    assert cost_of("claude-sonnet-5-5", 5_700, 400, S) == pytest.approx(0.0154)
    assert cost_of("claude-haiku-5-5", 5_700, 400, S) == pytest.approx(0.00077)


def test_a_charge_may_overdraw_and_the_check_then_waits_for_the_refill():
    clock = [0.0]
    lim = MemoryRateLimiter(clock=lambda: clock[0])
    day = Bucket("ai:user:day:u", 0.30, 0.30 / 1440)
    assert lim.take([day], need=None, spend=0.40).allowed      # one request ran over
    refused = lim.take([day], need=0.0001, spend=0.0)
    assert not refused.allowed and refused.refused_by == day.key
    # $0.1001 to refill at $0.30 a day: about 8 hours.
    assert refused.retry_after == pytest.approx(0.1001 / 0.30 * 86_400)
    clock[0] = refused.retry_after + 1
    assert lim.take([day], need=0.0001, spend=0.0).allowed


def test_nobody_paying_or_an_unpriced_model_is_never_charged(ids):
    assert charge_call("claude-sonnet-5-5", 100_000, 1_000) == 0.0       # no spender
    charge_to(*ids)
    assert charge_call("gemini-3.1-flash-lite", 100_000, 1_000) == 0.0   # background model


def test_a_person_over_their_day_is_refused_with_a_wait_in_minutes_or_hours(ids):
    user, org = ids
    assert rate_limit.question_decision(user, org).allowed
    charge_to(user, org)
    # ~21 typical Sonnet questions' worth in one go.
    assert charge_call("claude-sonnet-5-5", 150_000, 2_000) == pytest.approx(0.32)
    decision = rate_limit.question_decision(user, org)
    assert not decision.allowed and decision.refused_by == f"ai:user:day:{user}"
    message = rate_limit.refusal_message(decision.retry_after, decision.refused_by)
    assert message.startswith("You've reached the AI usage limit for now.")
    assert message.endswith(("minutes.", "hour.", "hours."))
    # A colleague is unaffected: the company's day still has room.
    assert rate_limit.question_decision(str(uuid.uuid4()), org).allowed


def test_the_company_day_stops_everyone_in_it(ids):
    user, org = ids
    for _ in range(11):                       # 11 people x $0.30 > the company's $3.00
        charge_to(str(uuid.uuid4()), org)
        charge_call("claude-sonnet-5-5", 140_000, 2_000)
    decision = rate_limit.question_decision(user, org)
    assert not decision.allowed and decision.refused_by == f"ai:org:day:{org}"
    assert rate_limit.question_decision(user, str(uuid.uuid4())).allowed   # another company


def test_a_refused_question_spends_no_question_allowance(ids):
    user, org = ids
    charge_to(user, org)
    charge_call("claude-sonnet-5-5", 150_000, 2_000)
    for _ in range(10):                       # far past the question burst of 5
        assert not rate_limit.question_decision(user, org).allowed
    assert rate_limit.refusal_message(4, "q:user:x") == "Too many requests. Please try again in 4 seconds."


def test_every_provider_call_is_charged_to_the_asker(ids, monkeypatch):
    charged = []
    monkeypatch.setattr(ai_spend, "charge_call", lambda m, i, o: charged.append((m, i, o)))
    provider = OpenAICompatProvider(model="claude-haiku-5-5", api_key="k", base_url="https://llm.example/v1")
    reply = SimpleNamespace(
        model="claude-haiku-5-5",
        usage=SimpleNamespace(prompt_tokens=5_000, completion_tokens=300),
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None,
                                                         model_dump=lambda: {}),
                                 finish_reason="stop")],
    )
    monkeypatch.setattr(provider, "_create", lambda kwargs: reply)
    provider.generate("q")
    provider.generate_with_tools([{"role": "user", "content": "q"}], tools=[])
    assert charged == [("claude-haiku-5-5", 5_000, 300)] * 2


def test_a_charge_that_cannot_be_recorded_never_fails_the_answer(ids, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("store down")
    monkeypatch.setattr(ai_spend, "charge_call", boom)
    provider = OpenAICompatProvider(model="claude-haiku-5-5", api_key="k", base_url="https://llm.example/v1")
    reply = SimpleNamespace(model="m", usage=None,
                            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"), finish_reason="stop")])
    monkeypatch.setattr(provider, "_create", lambda kwargs: reply)
    assert provider.generate("q") == "ok"


def test_redis_charges_and_checks_the_same_way(redis_url):
    lim = RedisRateLimiter(redis_url)
    key = f"ai:user:day:{uuid.uuid4()}"
    day = Bucket(key, 0.30, 0.30 / 1440)
    assert lim.take([day], need=0.0001, spend=0.0).allowed
    assert lim.take([day], need=None, spend=0.40).allowed
    refused = lim.take([day], need=0.0001, spend=0.0)
    assert not refused.allowed and refused.refused_by == key
    assert refused.retry_after == pytest.approx(0.1001 / 0.30 * 86_400, rel=0.01)
    import redis

    # Kept until it has refilled: an overdrawn bucket must not expire as if full.
    assert redis.Redis.from_url(redis_url).ttl("rl:" + key) > 86_400


# --- Tokens a minute, per model (as Anthropic, LiteLLM and Kong limit) -------

SONNET, HAIKU = "claude-sonnet-5-5", "claude-haiku-5-5"


def test_each_model_has_its_own_minute_of_tokens(ids):
    user, org = ids
    charge_to(user, org)
    charge_call(SONNET, 61_000, 500)               # past a person's 60k input a minute
    sonnet = rate_limit.question_decision(user, org, model=SONNET)
    assert not sonnet.allowed and sonnet.refused_by == f"ai:user:tin:{SONNET}:{user}"
    # 1k tokens over at 60k a minute: about a second, said in seconds, never hours.
    assert 0 < sonnet.retry_after <= 2
    assert rate_limit.refusal_message(sonnet.retry_after, sonnet.refused_by).endswith(("second.", "seconds."))
    # Haiku is another model: its own buckets are full.
    assert rate_limit.question_decision(user, org, model=HAIKU).allowed
    # A colleague is unaffected.
    assert rate_limit.question_decision(str(uuid.uuid4()), org, model=SONNET).allowed


def test_output_tokens_are_limited_apart_from_input(ids):
    user, org = ids
    charge_to(user, org)
    charge_call(HAIKU, 1_000, 8_500)               # past a person's 8k output a minute
    decision = rate_limit.question_decision(user, org, model=HAIKU)
    assert not decision.allowed and decision.refused_by == f"ai:user:tout:{HAIKU}:{user}"


def test_the_company_minute_stops_everyone_on_that_model(ids):
    user, org = ids
    for _ in range(8):                             # 8 x 55k > the company's 400k a minute
        charge_to(str(uuid.uuid4()), org)
        charge_call(HAIKU, 55_000, 100)
    decision = rate_limit.question_decision(user, org, model=HAIKU)
    assert not decision.allowed and decision.refused_by == f"ai:org:tin:{HAIKU}:{org}"
    assert rate_limit.question_decision(user, org, model=SONNET).allowed


def test_a_dated_id_shares_its_models_buckets_and_an_unlisted_model_has_none(ids):
    user, org = ids
    ins, outs = ai_spend.token_buckets(user, org, "claude-sonnet-5-5-20261001", S)
    assert [b.key for b in ins] == [f"ai:user:tin:{SONNET}:{user}", f"ai:org:tin:{SONNET}:{org}"]
    assert [b.burst for b in ins + outs] == [60_000, 400_000, 8_000, 40_000]
    assert ai_spend.token_buckets(user, org, "gemini-3.5-flash-lite", S) == ([], [])


def test_token_limits_are_read_from_one_setting(monkeypatch):
    monkeypatch.setenv("AI_TOKEN_LIMITS", "claude-haiku-5-5=100:10:0:0, broken, x=1:2")
    limits = RateLimitSettings.from_env().ai_token_limits
    assert limits == (("claude-haiku-5-5", 100, 10, 0, 0),)   # 0 turns the company's off
    ins, outs = ai_spend.token_buckets("u", "o", HAIKU, RateLimitSettings.from_env())
    assert [b.key for b in ins + outs] == [f"ai:user:tin:{HAIKU}:u", f"ai:user:tout:{HAIKU}:u"]


def test_a_token_refusal_is_logged_by_its_limits_name():
    assert rate_limit._describe(f"ai:org:tout:{SONNET}:o1") == f"ai-tokens:company-output-minute org={SONNET}:o1"
