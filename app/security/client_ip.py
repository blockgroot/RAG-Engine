"""Resolve the real client IP behind a reverse proxy.

``request.client.host`` is the immediate peer (the proxy), which collapsed
every per-IP limit into one global bucket. uvicorn ignores
``X-Forwarded-For`` unless the peer is localhost.

Header trust: XFF's leftmost entry is client-controlled and spoofable, so
it is the LAST resort. Edge-written single-value headers come first, and
``CLIENT_IP_HEADER`` pins one header for a deployment that knows its
topology (the only fully unambiguous option).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Single-value headers the edge overwrites, in preference order: a client
# cannot prepend its own.
_TRUSTED_SINGLE_VALUE_HEADERS = ("x-vercel-forwarded-for", "x-real-ip")

_UNKNOWN = "unknown"


def resolve_client_ip(request) -> str:
    """Best available client identifier for rate limiting. Never raises or
    returns empty: an unknown caller shares the ``"unknown"`` bucket, the safe
    direction for a limiter."""
    headers = getattr(request, "headers", {}) or {}

    pinned = (os.getenv("CLIENT_IP_HEADER") or "").strip().lower()
    if pinned:
        value = _first_value(headers.get(pinned))
        if value:
            return value
        logger.debug("CLIENT_IP_HEADER=%s was set but absent on the request", pinned)

    for header in _TRUSTED_SINGLE_VALUE_HEADERS:
        value = _first_value(headers.get(header))
        if value:
            return value

    # Last resort: spoofable, but better than one bucket for everyone.
    forwarded = _first_value(headers.get("x-forwarded-for"))
    if forwarded:
        return forwarded

    client = getattr(request, "client", None)
    return getattr(client, "host", None) or _UNKNOWN


def _first_value(raw: str | None) -> str | None:
    """Leftmost entry of a possibly comma-separated header value."""
    if not raw:
        return None
    first = raw.split(",")[0].strip()
    return first or None
