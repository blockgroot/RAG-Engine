"""Which rate limits a request takes (the counting is ``app/ratelimit``).

* Every API call: the person's request bucket, or the IP's when signed out
  (``limit_request``, a dependency on every router a person calls).
* A question, which costs the model (chat, Agent, charts, scheduler setup,
  Slack): the person's question bucket AND their company's, taken together
  (``limit_question``). The company's grows with its members: every company
  shares one AI account, and per-person limits alone let a large company use
  all of it.
* Anything that sends an email (login link, email change): its own small
  bucket, per IP or person (``limit_email``).

And places, for answers being written at once (``take_place``): a person's
own few, and everyone's shared cap. Shared across servers in Redis.

Every refusal is logged with the limit that refused it (never the question,
never an address): that log is how the numbers get tuned.

A limiter fault lets the request through and logs an error: the limiter must
never be what takes the product down.
"""

from __future__ import annotations

import logging
import math
import time
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from ..config.settings import RateLimitSettings
from ..core.exceptions import AuthError, RateLimiterError
from ..db.connection import get_connection
from ..ratelimit import Bucket, Decision, build_rate_limiter
from .client_ip import resolve_client_ip

logger = logging.getLogger(__name__)

# A place a dead server never gives back frees itself after this. Longer than
# any answer takes to write.
PLACE_LEASE_SECONDS = 300

# Members change rarely; counting them on every question would be a query per
# question.
_MEMBER_COUNT_TTL_SECONDS = 300
_member_counts: dict[str, tuple[float, int]] = {}


def refusal_message(retry_after: float) -> str:
    seconds = max(1, math.ceil(retry_after))
    unit = "second" if seconds == 1 else "seconds"
    return f"Too many requests. Please try again in {seconds} {unit}."


def question_decision(
    user_id: str, org_id: str, *, settings: RateLimitSettings | None = None
) -> Decision:
    """The person's and the company's question buckets, taken together.

    Returns the decision rather than raising, for Slack, which answers in the
    thread instead of with an HTTP status.
    """
    settings = settings or RateLimitSettings.from_env()
    org_rate = max(
        float(settings.org_questions_min),
        settings.org_questions_per_member * _member_count(org_id),
    )
    return _take(
        [
            Bucket(f"q:user:{user_id}", settings.question_burst, settings.questions_per_minute),
            Bucket(f"q:org:{org_id}", math.ceil(org_rate), org_rate),
        ],
        settings,
    )


def limit_question(session) -> None:
    _raise_if_refused(question_decision(session.user_id, session.org_id))


def limit_email(key: str) -> None:
    """``key`` is ``ip:<address>`` for a signed-out caller, ``user:<id>`` otherwise."""
    settings = RateLimitSettings.from_env()
    _raise_if_refused(
        _take([Bucket(f"email:{key}", settings.email_burst, settings.emails_per_minute)], settings)
    )


def limit_request(request: Request) -> None:
    """Router dependency: every call, per person, or per IP when signed out.

    Reads the session's signed claims only (no database): a revoked session is
    still that person for counting, and the route itself refuses it.
    """
    from ..api.deps import SESSION_COOKIE_NAME
    from ..auth.session import decode_session_token

    who = f"ip:{resolve_client_ip(request)}"
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        try:
            who = f"user:{decode_session_token(token).user_id}"
        except AuthError:
            pass
    settings = RateLimitSettings.from_env()
    _raise_if_refused(
        _take([Bucket(f"req:{who}", settings.request_burst, settings.requests_per_minute)], settings)
    )


def report_store(settings: RateLimitSettings | None = None) -> str:
    """Log, once at startup, where rate limits are counted, and return it.

    Working Redis is otherwise silent (only failures log), so a deploy could
    not show that ``REDIS_URL`` took effect. Logged as a warning because only
    warnings and above reach the host's log. The host only, never the URL:
    it carries the password.
    """
    settings = settings or RateLimitSettings.from_env()
    if not settings.enabled:
        line = "Rate limits: switched off (RATE_LIMIT_ENABLED=false)"
    elif not settings.redis_url:
        line = "Rate limits: REDIS_URL is not set, each server is counting on its own"
    else:
        parts = urlsplit(settings.redis_url)
        where = f"{parts.hostname}:{parts.port or 6379}"
        try:
            build_rate_limiter(settings).take([Bucket("startup-check", 1, 60)])
            line = f"Rate limits: Redis at {where}, connected"
        except RateLimiterError as exc:
            line = (
                f"Rate limits: Redis at {where} NOT reachable ({exc.cause or exc}); "
                "requests go ahead without limits until it is"
            )
    logger.warning(line)
    return line


def take_place(key: str, limit: int, settings: RateLimitSettings) -> str | None:
    """Hold one of ``limit`` places. None: all are held. "": not tracked (the
    cap is off, or the store failed and the request goes ahead)."""
    if limit <= 0:
        return ""
    try:
        token = build_rate_limiter(settings).acquire(key, limit, PLACE_LEASE_SECONDS)
    except RateLimiterError as exc:
        logger.error("Rate limiter unavailable, answer let through: %s", exc.cause or exc)
        return ""
    if token is None:
        logger.warning("rate limit refused: limit=%s", _describe(key))
    return token


def give_back_place(key: str, token: str | None, settings: RateLimitSettings) -> None:
    if not token:
        return
    try:
        build_rate_limiter(settings).release(key, token)
    except RateLimiterError as exc:
        # The lease frees it.
        logger.warning("Could not release rate-limit place %s: %s", _describe(key), exc)


def _take(buckets: list[Bucket], settings: RateLimitSettings) -> Decision:
    if not settings.enabled:
        return Decision(allowed=True)
    try:
        decision = build_rate_limiter(settings).take(buckets)
    except RateLimiterError as exc:
        logger.error("Rate limiter unavailable, request let through: %s", exc.cause or exc)
        return Decision(allowed=True)
    if not decision.allowed:
        logger.warning(
            "rate limit refused: limit=%s retry_after=%ss",
            _describe(decision.refused_by),
            max(1, math.ceil(decision.retry_after)),
        )
    return decision


# Bucket key prefix -> the limit's name in the refusal log.
_LIMIT_NAMES = {
    "q:user": "question:person",
    "q:org": "question:company",
    "email:user": "email:person",
    "email:ip": "email:ip",
    "req:user": "request:person",
    "req:ip": "request:ip",
    "answers:user": "answers-at-once:person",
}


def _describe(key: str) -> str:
    """The limit's name and whose it is, for the log. An IP address is left
    out: it says nothing about the numbers and is personal data."""
    if key == "answers:all":
        return "answers-at-once:all"
    # By prefix, not by splitting: an IPv6 address is full of colons.
    for prefix, name in _LIMIT_NAMES.items():
        if key.startswith(prefix + ":"):
            if prefix.endswith(":ip"):
                return name
            return f"{name} {prefix.split(':')[1]}={key[len(prefix) + 1:]}"
    return "unknown"


def _raise_if_refused(decision: Decision) -> None:
    if decision.allowed:
        return
    raise HTTPException(
        status_code=429,
        detail=refusal_message(decision.retry_after),
        headers={"Retry-After": str(max(1, math.ceil(decision.retry_after)))},
    )


def _member_count(org_id: str) -> int:
    now = time.monotonic()
    cached = _member_counts.get(org_id)
    if cached and now - cached[0] < _MEMBER_COUNT_TTL_SECONDS:
        return cached[1]
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT count(*) FROM users WHERE org_id = %s", (org_id,)
            ).fetchone()
        count = int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001 - sizing must never refuse a question
        # The company's minimum applies; the next question tries again.
        logger.warning("Could not count members of org %s: %s", org_id, exc)
        return 0
    _member_counts[org_id] = (now, count)
    return count
