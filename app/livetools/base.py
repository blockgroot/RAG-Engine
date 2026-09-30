"""What a live read returns, and the constants that bound it.

Constants, not settings (plan D15): configuration nobody tunes is
configuration that drifts. ``live_tool_calls`` will say if any needs to move.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

#: Objects refreshed per question. Mode A reads the best hits only.
MAX_REFRESHES = 2
#: Per read. A live read is an enhancement: past this the indexed copy answers.
TIMEOUT_SECONDS = 6.0
#: Per live block, like any other context. Truncation is marked, never silent.
MAX_CHARS = 6000
#: Newest comments on a Linear issue.
LINEAR_COMMENTS = 5
#: How many distinct documents at the top of the hits are considered at all.
CANDIDATE_DOCUMENTS = 5
#: A tool whose last SUCCESSFUL sync is younger than this is not read live:
#: its synced copy is already fresh, and the read would only cost ~2 s.
FRESH_SECONDS = 15 * 60

# Outcomes (the audit row's `outcome`). Only NOT_ACCESSIBLE withholds the
# indexed copy; every other failure falls back to it (plan D16, §5).
OK = "ok"
NOT_ACCESSIBLE = "not_accessible"
NOT_CONNECTED = "not_connected"
TIMEOUT = "timeout"
REAUTH = "reauth"
RATE_LIMITED = "rate_limited"
GUARD_FLAGGED = "guard_flagged"
ERROR = "error"


@dataclass(frozen=True)
class ProviderRead:
    """What a provider module hands back: an outcome, and text only when ``ok``."""

    outcome: str
    text: str = ""
    #: The provider's own reason for a failure (a GraphQL code, a 403 reason),
    #: logged so an unrecognised one can be mapped. Never contains the token.
    reason: str | None = None


@dataclass(frozen=True)
class LiveRead:
    """One refreshed object. ``text`` is set only when ``outcome == "ok"``."""

    provider: str
    document_id: str
    external_id: str
    outcome: str
    text: str = ""
    fetched_at: datetime | None = None
    truncated: bool = False


@dataclass(frozen=True)
class LiveRefresh:
    """Everything the pipeline needs from one gateway pass."""

    reads: list[LiveRead] = field(default_factory=list)

    @property
    def blocks(self) -> list[str]:
        """Prompt-ready live blocks, in hit order."""
        return [r.text for r in self.reads if r.outcome == OK and r.text]

    @property
    def refreshed(self) -> frozenset[str]:
        """Documents whose live block is in the prompt: their synced chunks
        are superseded and must not ride along beside it (plan D5)."""
        return frozenset(r.document_id for r in self.reads if r.outcome == OK and r.text)

    @property
    def withheld(self) -> dict[str, str]:
        """``document_id -> provider`` for objects the provider says are gone
        or no longer readable: their indexed copies must not answer either."""
        return {r.document_id: r.provider for r in self.reads if r.outcome == NOT_ACCESSIBLE}

    @property
    def sources(self) -> list[dict]:
        """``done.live_sources``: which connectors answered live, and when."""
        return [
            {"provider": r.provider, "fetched_at": r.fetched_at.isoformat()}
            for r in self.reads
            if r.outcome == OK and r.text and r.fetched_at is not None
        ]
