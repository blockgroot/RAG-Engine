"""AI spend limits: what answering really costs, in dollars.

Request limits (``rate_limit.py``) count questions, but one question can cost
ten times another (Claude benchmarks: median ~6k tokens, largest ~33k) and the
answering models differ twentyfold in price. So every paid model call is
charged its real cost (tokens x that model's price) to the person's and the
company's dollar buckets, and a question is refused once one is empty
(``rate_limit.question_decision``). The cost is known only after the call, so a
bucket may go below zero by one request; the next waits until it refills.

Who pays is set per request with ``charge_to``, where the answering model is
chosen (inside the stream that answers: a ContextVar set in the route is not
reliably the context the answer runs in). A call with nobody set, or on a model
with no price (Gemini background work), is not charged.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

from ..config.settings import RateLimitSettings
from ..core.exceptions import RateLimiterError
from ..ratelimit import Bucket, build_rate_limiter

logger = logging.getLogger(__name__)

_MINUTES_PER_DAY = 1440

# (user_id, org_id) paying for model calls in this request.
_SPENDER: ContextVar[tuple[str | None, str | None]] = ContextVar(
    "ai_spender", default=(None, None)
)


def charge_to(user_id: str | None, org_id: str | None) -> None:
    """Model calls from here on in this request are paid by this person and company."""
    _SPENDER.set((user_id, org_id))


def price_of(model: str | None, settings: RateLimitSettings) -> tuple[float, float] | None:
    """``($ per million input, $ per million output)``, or None when not paid from the credit.

    A vendor prefix (``anthropic/``) and a dated suffix (``-20261009``) still
    match their base id; the longest listed id wins, so ``claude-sonnet-5-5`` is
    never priced as ``claude-sonnet-5``.
    """
    name = (model or "").strip().lower().rsplit("/", 1)[-1].replace(".", "-")
    best: tuple[str, float, float] | None = None
    for key, per_in, per_out in settings.ai_prices:
        if name == key or (name.startswith(key) and name[len(key)] in "-@:"):
            if best is None or len(key) > len(best[0]):
                best = (key, per_in, per_out)
    return (best[1], best[2]) if best else None


def cost_of(
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    settings: RateLimitSettings,
) -> float:
    # ponytail: one price per model; Haiku 5.5 charges 5x above 100k-token
    # prompts, which no answer reaches (largest measured: ~33k).
    price = price_of(model, settings)
    if price is None:
        return 0.0
    return ((input_tokens or 0) * price[0] + (output_tokens or 0) * price[1]) / 1_000_000


def spend_buckets(
    user_id: str | None, org_id: str | None, settings: RateLimitSettings
) -> list[Bucket]:
    """The person's minute and day buckets and the company's day bucket, in
    dollars. A day bucket refills evenly over the day, so a limit hit at noon
    frees a little at a time rather than all at midnight."""
    buckets = []
    if user_id and settings.ai_person_dollars_per_minute and settings.ai_person_dollars_burst:
        buckets.append(Bucket(f"ai:user:min:{user_id}", settings.ai_person_dollars_burst,
                              settings.ai_person_dollars_per_minute))
    if user_id and settings.ai_person_dollars_per_day:
        buckets.append(Bucket(f"ai:user:day:{user_id}", settings.ai_person_dollars_per_day,
                              settings.ai_person_dollars_per_day / _MINUTES_PER_DAY))
    if org_id and settings.ai_company_dollars_per_day:
        buckets.append(Bucket(f"ai:org:day:{org_id}", settings.ai_company_dollars_per_day,
                              settings.ai_company_dollars_per_day / _MINUTES_PER_DAY))
    return buckets


def charge_call(
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    settings: RateLimitSettings | None = None,
) -> float:
    """Charge one finished model call to whoever this request is for.

    Returns the dollars charged (0 when nobody pays or the model is unpriced).
    A store fault is logged and the call stands: accounting never fails an answer.
    """
    user_id, org_id = _SPENDER.get()
    if not (user_id or org_id):
        return 0.0
    settings = settings or RateLimitSettings.from_env()
    if not settings.enabled:
        return 0.0
    cost = cost_of(model, input_tokens, output_tokens, settings)
    buckets = spend_buckets(user_id, org_id, settings)
    if cost <= 0 or not buckets:
        return 0.0
    try:
        build_rate_limiter(settings).take(buckets, need=None, spend=cost)
    except RateLimiterError as exc:
        logger.error("Could not charge AI spend of $%.4f: %s", cost, exc.cause or exc)
        return 0.0
    return cost
