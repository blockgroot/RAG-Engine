"""Token buckets in this process's memory.

For development, tests and a single-process install with no Redis. Each
process counts on its own, so N instances allow N times the limit: production
with more than one instance sets ``REDIS_URL``.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Sequence

from .base import Bucket, Decision, RateLimiter

# Bound the table: past this, buckets idle long enough to be full are dropped.
_MAX_KEYS = 100_000


class MemoryRateLimiter(RateLimiter):
    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, at)
        self._places: dict[str, dict[str, float]] = {}  # key -> {token: expires}

    def take(
        self, buckets: Sequence[Bucket], *, need: float | None = 1.0, spend: float = 1.0
    ) -> Decision:
        with self._lock:
            now = self._clock()
            tokens = [self._refilled(b, now) for b in buckets]
            waits = [
                ((need - t) * 60 / b.per_minute if need is not None and t < need else 0.0, b.key)
                for b, t in zip(buckets, tokens)
            ]
            wait, refused_by = max(waits, default=(0.0, ""))
            spent = spend if wait == 0 else 0.0
            for b, t in zip(buckets, tokens):
                self._state[b.key] = (t - spent, now)
            if len(self._state) > _MAX_KEYS:
                self._prune(now)
            if wait == 0:
                return Decision(allowed=True)
            return Decision(allowed=False, retry_after=wait, refused_by=refused_by)

    def acquire(self, key: str, limit: int, lease_seconds: float) -> str | None:
        with self._lock:
            now = self._clock()
            held = {t: e for t, e in self._places.get(key, {}).items() if e > now}
            if len(held) >= limit:
                self._places[key] = held
                return None
            token = uuid.uuid4().hex
            held[token] = now + lease_seconds
            self._places[key] = held
            return token

    def release(self, key: str, token: str) -> None:
        with self._lock:
            self._places.get(key, {}).pop(token, None)

    def _refilled(self, b: Bucket, now: float) -> float:
        tokens, at = self._state.get(b.key, (float(b.burst), now))
        return min(float(b.burst), tokens + (now - at) * b.per_minute / 60)

    def _prune(self, now: float) -> None:
        # ponytail: assumes the default request bucket's refill time; a bucket
        # dropped early only forgets a partly used allowance.
        stale = [k for k, (t, at) in self._state.items() if now - at > 60 and t >= 0]
        for key in stale:
            del self._state[key]
