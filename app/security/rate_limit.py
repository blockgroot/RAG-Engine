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

A limiter fault lets the request through and logs an error: the limiter must
never be what takes the product down.
"""

from __future__ import annotations

import logging
import math
import time

from fastapi import HTTPException, Request

from ..config.settings import RateLimitSettings
from ..core.exceptions import AuthError, RateLimiterError
from ..db.connection import get_connection
from ..ratelimit import Bucket, Decision, build_rate_limiter
from .client_ip import resolve_client_ip

logger = logging.getLogger(__name__)

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


def _take(buckets: list[Bucket], settings: RateLimitSettings) -> Decision:
    if not settings.enabled:
        return Decision(allowed=True)
    try:
        return build_rate_limiter(settings).take(buckets)
    except RateLimiterError as exc:
        logger.error("Rate limiter unavailable, request let through: %s", exc.cause or exc)
        return Decision(allowed=True)


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
