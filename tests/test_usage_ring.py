"""The usage ring: how much of today's AI use is left on Handbook's model.

Read only (``RateLimiter.levels``): showing usage must never spend it. Memory
limiter by default (conftest clears REDIS_URL); one test runs a real Redis.
"""

from __future__ import annotations

import uuid

import pytest

from app.ratelimit import Bucket, MemoryRateLimiter, RedisRateLimiter
from app.security import rate_limit
from app.security.ai_spend import charge_call, charge_to

HAIKU = "claude-haiku-5-5"


@pytest.fixture(autouse=True)
def _nobody_paying():
    charge_to(None, None)
    yield
    charge_to(None, None)


@pytest.fixture
def ids(monkeypatch):
    monkeypatch.setattr(rate_limit, "_member_count", lambda org: 30)
    return str(uuid.uuid4()), str(uuid.uuid4())


def test_the_ring_shows_the_share_of_the_day_left(ids):
    user, org = ids
    assert rate_limit.usage(user, org, HAIKU) == {"limited": True, "left": 1.0, "whose": "person", "retry_after": 0}
    charge_to(user, org)
    charge_call(HAIKU, 2_000_000, 100_000)                            # $0.25 of the person's $0.50
    shown = rate_limit.usage(user, org, HAIKU)
    assert shown["left"] == pytest.approx(0.5, abs=0.01) and shown["whose"] == "person"
    assert rate_limit.usage(user, org)["left"] == shown["left"]      # no model: the default's


def test_the_ring_names_the_company_when_its_day_runs_lowest(ids):
    user, org = ids
    for _ in range(6):                                                # colleagues: 6 x $0.50 of $3.30
        charge_to(str(uuid.uuid4()), org)
        charge_call(HAIKU, 4_000_000, 200_000)
    shown = rate_limit.usage(user, org, HAIKU)
    assert shown["whose"] == "company" and shown["left"] == pytest.approx(0.09, abs=0.01)


def test_an_overdrawn_day_shows_empty_with_the_wait(ids):
    user, org = ids
    charge_to(user, org)
    charge_call(HAIKU, 4_000_000, 400_000)                            # $0.60: past $0.50
    shown = rate_limit.usage(user, org, HAIKU)
    assert shown["left"] == 0.0 and shown["retry_after"] > 0
    assert not rate_limit.question_decision(user, org, model=HAIKU).allowed


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
    lim = RedisRateLimiter(redis_url)
    day = Bucket(f"ai:user:day:{uuid.uuid4()}", 0.5, 0.5 / 1440)
    assert lim.levels([day]) == [0.5]
    lim.take([day], need=None, spend=0.2)
    assert lim.levels([day]) == pytest.approx([0.3], abs=1e-4)
