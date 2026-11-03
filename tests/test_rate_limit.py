"""Token-bucket rate limits: the counting, person + company together, the wiring."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

from app.config.settings import RateLimitSettings
from app.core.exceptions import RateLimiterError
from app.ratelimit import Bucket, MemoryRateLimiter, RedisRateLimiter
from app.security import rate_limit


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _person(burst: int = 5, per_minute: float = 10) -> Bucket:
    return Bucket("q:user:a", burst, per_minute)


# --- the counting, in memory -------------------------------------------------


def test_burst_then_refused_with_seconds_until_the_next():
    limiter = MemoryRateLimiter(clock=Clock())
    assert all(limiter.take([_person()]).allowed for _ in range(5))
    refused = limiter.take([_person()])
    assert not refused.allowed
    assert refused.retry_after == pytest.approx(6.0)  # 10 a minute: one every 6 s


def test_refills_at_the_steady_rate():
    clock = Clock()
    limiter = MemoryRateLimiter(clock=clock)
    for _ in range(5):
        limiter.take([_person()])
    clock.now += 6
    assert limiter.take([_person()]).allowed
    assert not limiter.take([_person()]).allowed


def test_a_refusal_takes_from_no_bucket():
    """A person refused by their own limit must not spend their company's."""
    limiter = MemoryRateLimiter(clock=Clock())
    org = Bucket("q:org:x", 3, 3)
    assert limiter.take([_person(burst=1), org]).allowed
    assert not limiter.take([_person(burst=1), org]).allowed
    other = Bucket("q:user:b", 5, 10)
    assert limiter.take([other, org]).allowed
    assert limiter.take([other, org]).allowed  # the refusal above left 2


# --- the counting, in a real Redis -------------------------------------------


@pytest.fixture(scope="module")
def redis_url():
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("redis-server not installed")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [binary, "--port", str(port), "--save", "", "--appendonly", "no"],
        stdout=subprocess.DEVNULL,
    )
    url = f"redis://127.0.0.1:{port}/0"
    import redis

    for _ in range(50):
        try:
            redis.Redis.from_url(url).ping()
            break
        except redis.ConnectionError:
            time.sleep(0.05)
    yield url
    proc.terminate()
    proc.wait()


def test_redis_burst_refusal_and_expiry(redis_url):
    import redis

    limiter = RedisRateLimiter(redis_url)
    bucket = Bucket("test:burst", 3, 60)
    assert all(limiter.take([bucket]).allowed for _ in range(3))
    refused = limiter.take([bucket])
    assert not refused.allowed
    assert 0 < refused.retry_after <= 1.0  # 60 a minute: one a second
    ttl = redis.Redis.from_url(redis_url).ttl("rl:test:burst")
    assert 0 < ttl <= 4  # gone once it would be full again


def test_redis_takes_all_buckets_or_none(redis_url):
    limiter = RedisRateLimiter(redis_url)
    org = Bucket("test:org", 2, 1)
    assert limiter.take([Bucket("test:p1", 1, 1), org]).allowed
    assert not limiter.take([Bucket("test:p1", 1, 1), org]).allowed
    assert limiter.take([Bucket("test:p2", 1, 1), org]).allowed  # org still had one
    assert not limiter.take([Bucket("test:p3", 1, 1), org]).allowed


def test_unreachable_redis_is_an_error_and_requests_go_through():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again: nothing listens
    limiter = RedisRateLimiter(f"redis://127.0.0.1:{port}/0")
    with pytest.raises(RateLimiterError):
        limiter.take([_person()])

    settings = RateLimitSettings(redis_url=f"redis://127.0.0.1:{port}/0")
    assert rate_limit._take([_person()], settings).allowed


# --- which buckets a request takes -------------------------------------------


def test_a_question_takes_the_person_and_the_company(monkeypatch):
    monkeypatch.setattr(rate_limit, "_member_count", lambda org_id: 1)
    settings = RateLimitSettings(
        question_burst=5, org_questions_per_member=1, org_questions_min=2
    )
    assert rate_limit.question_decision("u1", "org", settings=settings).allowed
    assert rate_limit.question_decision("u2", "org", settings=settings).allowed
    # Both people still have their own allowance; the company's is spent.
    assert not rate_limit.question_decision("u3", "org", settings=settings).allowed
    assert rate_limit.question_decision("u1", "other-org", settings=settings).allowed


def test_the_company_limit_grows_with_its_members(monkeypatch):
    monkeypatch.setattr(rate_limit, "_member_count", lambda org_id: 10)
    settings = RateLimitSettings(
        question_burst=1, org_questions_per_member=1, org_questions_min=2
    )
    # 10 members -> room for 10 at once, not the minimum of 2.
    assert all(
        rate_limit.question_decision(f"u{i}", "big", settings=settings).allowed
        for i in range(10)
    )
    assert not rate_limit.question_decision("u10", "big", settings=settings).allowed


def test_refusal_is_a_429_naming_the_wait():
    with pytest.raises(HTTPException) as exc:
        rate_limit._raise_if_refused(rate_limit.Decision(allowed=False, retry_after=5.2))
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "6"
    assert exc.value.detail == "Too many requests. Please try again in 6 seconds."
    assert rate_limit.refusal_message(0.4) == "Too many requests. Please try again in 1 second."


def test_switched_off_allows_everything():
    off = RateLimitSettings(enabled=False, question_burst=1)
    assert all(rate_limit._take([_person(burst=1)], off).allowed for _ in range(5))


def test_every_route_a_person_calls_is_limited():
    """A new route is limited without anyone remembering to add it; the
    senders that are not people (Slack, webhooks, the cron, the MCP host) are
    not, or real events would be dropped per IP."""
    from app.api.main import create_app

    not_people = ("/slack", "/webhooks", "/internal", "/mcp")
    unlimited = []
    for route in create_app().routes:
        if not isinstance(route, APIRoute) or route.path == "/health":
            continue
        limited = any(d.dependency is rate_limit.limit_request for d in route.dependencies)
        if route.path.startswith(not_people):
            assert not limited, route.path
        elif not limited:
            unlimited.append(route.path)
    assert unlimited == []
