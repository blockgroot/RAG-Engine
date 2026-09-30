"""Second Brain live connector reads (docs/plans/2026-09-29-live-connector-access.md).

**The index finds, the live call refreshes.** A live read only ever re-reads an
object that retrieval already returned -- and therefore already cleared -- for
this asker; the gateway never searches a provider. Only ``gateway.py`` ever
decrypts a token for a live read, and nothing it returns carries one.
"""

from .base import LiveRead, LiveRefresh
from .context import LiveRequest, current_live_request, reset_live_request, use_live_request
from .gateway import refresh

__all__ = [
    "LiveRead",
    "LiveRefresh",
    "LiveRequest",
    "current_live_request",
    "refresh",
    "reset_live_request",
    "use_live_request",
]
