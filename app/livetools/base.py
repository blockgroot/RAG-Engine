"""What a live read returns, and the constants that bound it (constants, not
settings: nobody tunes them)."""

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
#: A tool synced successfully more recently than this is not read live.
FRESH_SECONDS = 15 * 60

# Audit outcomes. Only NOT_ACCESSIBLE withholds the indexed copy; others fall back to it.
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
    #: The provider's own failure reason, logged for mapping. Never the token.
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
    #: From the indexed ``documents`` row, never the live text, so citations match.
    title: str = ""
    source_uri: str | None = None

    @property
    def document_title(self) -> str | None:
        text = (self.title or "").strip()
        return text or None

    @property
    def source_provider(self) -> str:
        return self.provider


@dataclass(frozen=True)
class LiveRefresh:
    """Everything the pipeline needs from one gateway pass."""

    reads: list[LiveRead] = field(default_factory=list)

    def _shown(self) -> list[LiveRead]:
        return [r for r in self.reads if r.outcome == OK and r.text]

    @property
    def blocks(self) -> list[str]:
        """Prompt-ready live blocks, in hit order."""
        return [r.text for r in self._shown()]

    @property
    def cite_blocks(self) -> list[LiveRead]:
        """The same reads, as citation targets. Aligned with ``blocks``."""
        return self._shown()

    @property
    def refreshed(self) -> frozenset[str]:
        """Documents whose live block is in the prompt: their synced chunks are
        superseded and must not ride along beside it."""
        return frozenset(r.document_id for r in self.reads if r.outcome == OK and r.text)

    @property
    def withheld(self) -> dict[str, str]:
        """``document_id -> provider`` for objects the provider says are gone
        or no longer readable: their indexed copies must not answer either."""
        return {r.document_id: r.provider for r in self.reads if r.outcome == NOT_ACCESSIBLE}

    @property
    def sources(self) -> list[dict]:
        """``done.live_sources``: which connectors answered live, and when.
        ``document_id`` lets the chat show "live" only for a cited read
        (``api.chat._shown_live``); it is dropped before the browser."""
        return [
            {"provider": r.provider, "fetched_at": r.fetched_at.isoformat(),
             "document_id": r.document_id}
            for r in self.reads
            if r.outcome == OK and r.text and r.fetched_at is not None
        ]
