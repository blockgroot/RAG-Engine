"""The rate limiter contract: token buckets, taken all together or not at all.

A bucket holds up to ``burst`` requests and refills at ``per_minute``. A person
can ask a few questions quickly, then is held to the steady rate; someone
refused is told how many seconds until the next request is free, not to wait
out a whole window (the comparison is docs/plans/2026-11-03-rate-limiting.md).

Several buckets are taken in ONE step (the person's and their company's): a
request refused by either takes from neither, so a refused person never spends
their company's allowance.

The same buckets also hold money (AI spend, in dollars): checked before a
question with ``need`` just above zero and nothing spent, then charged the
call's real cost afterwards with no ``need`` at all, which may take the
balance below zero. A request's cost is only known once it has run.

Places are the other kind of limit: how many things (answers being written)
may be in progress at once. A place is held until released; one a dead server
never releases frees itself when its lease runs out.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Bucket:
    key: str
    burst: float
    per_minute: float


@dataclass(frozen=True)
class Decision:
    allowed: bool
    # Seconds until every bucket has room again; 0 when allowed.
    retry_after: float = 0.0
    # The key of the bucket that refused (the longest wait); "" when allowed.
    refused_by: str = ""


class RateLimiter(ABC):
    @abstractmethod
    def take(
        self, buckets: Sequence[Bucket], *, need: float | None = 1.0, spend: float = 1.0
    ) -> Decision:
        """Take ``spend`` from every bucket if each holds at least ``need``,
        else from none. One request is ``need=1, spend=1``; ``need=None``
        always spends (a charge, which may overdraw).

        Raises ``core.exceptions.RateLimiterError`` when the store fails.
        """
        raise NotImplementedError

    @abstractmethod
    def acquire(self, key: str, limit: int, lease_seconds: float) -> str | None:
        """Hold one of ``limit`` places under ``key``.

        Returns a token to release it with, or None when all are held. Raises
        ``core.exceptions.RateLimiterError`` when the store fails.
        """
        raise NotImplementedError

    @abstractmethod
    def release(self, key: str, token: str) -> None:
        """Give the place back. Releasing one already gone is fine."""
        raise NotImplementedError
