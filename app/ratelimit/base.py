"""The rate limiter contract: token buckets, taken all together or not at all.

A bucket holds up to ``burst`` requests and refills at ``per_minute``. A person
can ask a few questions quickly, then is held to the steady rate; someone
refused is told how many seconds until the next request is free, not to wait
out a whole window (the comparison is docs/plans/2026-11-03-rate-limiting.md).

Several buckets are taken in ONE step (the person's and their company's): a
request refused by either takes from neither, so a refused person never spends
their company's allowance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Bucket:
    key: str
    burst: int
    per_minute: float


@dataclass(frozen=True)
class Decision:
    allowed: bool
    # Seconds until every bucket has room again; 0 when allowed.
    retry_after: float = 0.0


class RateLimiter(ABC):
    @abstractmethod
    def take(self, buckets: Sequence[Bucket]) -> Decision:
        """Take one request from every bucket, or from none.

        Raises ``core.exceptions.RateLimiterError`` when the store fails.
        """
        raise NotImplementedError
