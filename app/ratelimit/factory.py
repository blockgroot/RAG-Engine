"""Single construction point for the rate limiter.

Redis when ``REDIS_URL`` is set, so every instance shares the counts; this
process's memory otherwise. Built once per process: the Redis client holds a
connection pool, and the memory limiter IS its state.
"""

from __future__ import annotations

import logging

from ..config.settings import RateLimitSettings
from .base import RateLimiter
from .memory import MemoryRateLimiter
from .redis_limiter import RedisRateLimiter

logger = logging.getLogger(__name__)

_cache: dict[str | None, RateLimiter] = {}


def clear_rate_limiter_cache() -> None:
    """Drop built limiters (tests)."""
    _cache.clear()


def build_rate_limiter(settings: RateLimitSettings | None = None) -> RateLimiter:
    settings = settings or RateLimitSettings.from_env()
    url = settings.redis_url
    limiter = _cache.get(url)
    if limiter is None:
        if url:
            limiter = RedisRateLimiter(url)
        else:
            logger.warning(
                "REDIS_URL is not set: rate limits are counted per process. "
                "Set it when running more than one instance."
            )
            limiter = MemoryRateLimiter()
        _cache[url] = limiter
    return limiter
