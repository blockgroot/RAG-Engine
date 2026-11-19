"""AI usage limits: tokens per minute per model, and dollars per day.

* Tokens a minute per answering model, input and output apart, for the
  person and the company (``token_buckets``).
* Dollars a day for the person and the company (``spend_buckets``), one
  wallet whichever model answers.

One question can cost ten times another and models differ twentyfold in
price, so every paid call is charged its real cost and a question is
refused once a bucket is empty (``rate_limit.question_decision``). Cost is
known only after the call, so a bucket may overdraw by one request.

``charge_to`` is set where the answering model is chosen (inside the
stream). Nobody set, or an unpriced model (Gemini background work): not
charged.
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


def _listed(model: str | None, rows: tuple[tuple, ...]) -> tuple | None:
    """The row whose model id ``model`` is, or None. Vendor prefixes and dated
    suffixes match their base id; the longest listed id wins."""
    name = (model or "").strip().lower().rsplit("/", 1)[-1].replace(".", "-")
    best: tuple | None = None
    for row in rows:
        key = row[0]
        if name == key or (name.startswith(key) and name[len(key)] in "-@:"):
            if best is None or len(key) > len(best[0]):
                best = row
    return best


def price_of(model: str | None, settings: RateLimitSettings) -> tuple[float, float] | None:
    """``($ per million input, $ per million output)``, or None when not paid from the credit."""
    row = _listed(model, settings.ai_prices)
    return (row[1], row[2]) if row else None


def token_buckets(
    user_id: str | None, org_id: str | None, model: str | None, settings: RateLimitSettings
) -> tuple[list[Bucket], list[Bucket]]:
    """``(input buckets, output buckets)`` for this model, person and company,
    each a minute of tokens. Keyed by the listed id (dated ids share)."""
    row = _listed(model, settings.ai_token_limits)
    if row is None:
        return [], []
    key, person_in, person_out, company_in, company_out = row
    ins: list[Bucket] = []
    outs: list[Bucket] = []
    for who, limit_in, limit_out, scope in (
        (user_id, person_in, person_out, "user"),
        (org_id, company_in, company_out, "org"),
    ):
        if who and limit_in:
            ins.append(Bucket(f"ai:{scope}:tin:{key}:{who}", limit_in, limit_in))
        if who and limit_out:
            outs.append(Bucket(f"ai:{scope}:tout:{key}:{who}", limit_out, limit_out))
    return ins, outs


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
    """The person's and the company's day buckets in dollars, refilling evenly
    over the day."""
    buckets = []
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
    """Charge one finished call: tokens to the model's minute buckets, cost to
    the dollar buckets. Returns dollars charged. A store fault is logged and
    the call stands."""
    user_id, org_id = _SPENDER.get()
    if not (user_id or org_id):
        return 0.0
    settings = settings or RateLimitSettings.from_env()
    if not settings.enabled:
        return 0.0
    ins, outs = token_buckets(user_id, org_id, model, settings)
    cost = cost_of(model, input_tokens, output_tokens, settings)
    dollars = spend_buckets(user_id, org_id, settings) if cost > 0 else []
    charges = [(b, n) for b, n in ((ins, input_tokens or 0), (outs, output_tokens or 0), (dollars, cost))
               if b and n > 0]
    try:
        limiter = build_rate_limiter(settings)
        for buckets, amount in charges:
            limiter.take(buckets, need=None, spend=amount)
    except RateLimiterError as exc:
        logger.error("Could not charge AI usage (%s in, %s out tokens, $%.4f): %s",
                     input_tokens, output_tokens, cost, exc.cause or exc)
        return 0.0
    return cost if dollars else 0.0
