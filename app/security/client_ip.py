"""Resolve the real client IP behind a reverse proxy.

Why this module exists (a measured, live defect)
------------------------------------------------
The magic-link endpoint rate-limits per client IP, and read that IP from
``request.client.host``. That is the IP of the **immediate peer**, which in this
deployment is never the user: the browser talks to Vercel, Vercel proxies
``/api/*`` to Render, and Render's own router fronts the container. So every
request arrived with the *same* host and the whole "per-IP" budget collapsed into
one global bucket shared by every user — which is worse than no protection was
meant to be, because it also means one script consuming the budget returns 429
to everybody's login.

uvicorn does not save us here: ``proxy_headers`` defaults to on, but
``forwarded_allow_ips`` defaults to ``127.0.0.1``, so ``X-Forwarded-For`` is
ignored unless the peer is localhost — and behind Render it never is.

Header trust (read this before changing the order)
--------------------------------------------------
``X-Forwarded-For`` is a list that each proxy appends to, so its **leftmost**
entry is whatever the original caller sent — i.e. client-controlled and
spoofable, which for a rate limiter means trivially bypassable (send a random
value per request and every request gets its own bucket). That is why the
leftmost XFF entry is the LAST resort here, and why platform-set single-value
headers are preferred: the edge writes them itself, so a client cannot dictate
them.

``CLIENT_IP_HEADER`` pins one header explicitly for a deployment that knows its
own topology; that is the only fully unambiguous option and is what a
security-sensitive deployment should set.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Single-value headers written by the edge itself, in preference order. Both are
# overwritten (not appended to) by the platform, so unlike X-Forwarded-For a
# client cannot prepend a value of its own choosing.
_TRUSTED_SINGLE_VALUE_HEADERS = ("x-vercel-forwarded-for", "x-real-ip")

_UNKNOWN = "unknown"


def resolve_client_ip(request) -> str:
    """Best available client identifier for rate-limiting purposes.

    Never raises and never returns an empty string: an unidentifiable caller
    falls back to ``"unknown"``, which is a *shared* bucket. That is deliberate
    — failing into one throttled bucket is the safe direction for a limiter,
    the opposite of the memory gate in ``app/jobs/worker.py`` which must fail
    open because a broken gauge there would block all ingestion.
    """
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

    # Last resort. Spoofable (see module docstring) — kept only because it is
    # still better than one bucket for the entire internet.
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
