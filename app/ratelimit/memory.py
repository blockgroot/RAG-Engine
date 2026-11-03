"""Token buckets in this process's memory.

For development, tests and a single-process install with no Redis. Each
process counts on its own, so N instances allow N times the limit: production
with more than one instance sets ``REDIS_URL``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence

from .base import Bucket, Decision, RateLimiter

# A flood from many addresses must not grow the table without end. Past this,
# buckets idle long enough to be full again are dropped (a full bucket is the
# same as no bucket).
_MAX_KEYS = 100_000


class MemoryRateLimiter(RateLimiter):
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, at)

    def take(self, buckets: Sequence[Bucket]) -> Decision:
        with self._lock:
            now = self._clock()
            tokens = [self._refilled(b, now) for b in buckets]
            wait = max(
                ((1 - t) * 60 / b.per_minute for b, t in zip(buckets, tokens) if t < 1),
                default=0.0,
            )
            spend = 1 if wait == 0 else 0
            for b, t in zip(buckets, tokens):
                self._state[b.key] = (t - spend, now)
            if len(self._state) > _MAX_KEYS:
                self._prune(now)
            return Decision(allowed=wait == 0, retry_after=wait)

    def _refilled(self, b: Bucket, now: float) -> float:
        tokens, at = self._state.get(b.key, (float(b.burst), now))
        return min(float(b.burst), tokens + (now - at) * b.per_minute / 60)

    def _prune(self, now: float) -> None:
        # ponytail: assumes the default request bucket's refill time; a bucket
        # dropped early only forgets a partly used allowance.
        stale = [k for k, (_, at) in self._state.items() if now - at > 60]
        for key in stale:
            del self._state[key]
