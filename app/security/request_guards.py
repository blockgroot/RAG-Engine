"""Two request guards in front of every route, as pure ASGI (``BaseHTTPMiddleware``
changes how a streamed answer is cancelled).

``BodyLimitMiddleware``: a body over the limit is refused with 413 before a route
reads it, by ``Content-Length`` or, without one, by counting while it arrives.
Uploads get their own cap plus room for the multipart wrapping.

``OriginCheckMiddleware``: a state-changing request whose ``Origin`` is not ours.
The session cookie is ``SameSite=Lax``, which already stops a cross-site POST
carrying it; this is the second layer (a same-site sibling, an old browser).
A request with no ``Origin`` (Slack, webhooks, curl) is not a browser's
cross-site request and passes.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_UPLOAD_PATH = re.compile(r"^/chat/conversations/[^/]+/attachments/?$")
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


async def _refuse(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app, *, max_bytes: int, upload_max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.upload_max_bytes = upload_max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        limit = (self.upload_max_bytes if _UPLOAD_PATH.match(scope.get("path") or "")
                 else self.max_bytes)
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            return await _refuse(send, 413, "The request is too large.")

        seen = 0
        started = False

        async def counted_receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body") or b"")
                if seen > limit:
                    raise _TooLarge
            return message

        async def tracked_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except _TooLarge:
            if not started:
                await _refuse(send, 413, "The request is too large.")


def _origin(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else None


class OriginCheckMiddleware:
    def __init__(self, app, *, allowed: list[str], mode: str) -> None:
        self.app = app
        self.allowed = {o for o in (_origin(a) for a in allowed) if o}
        self.mode = mode

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.mode == "off" or scope.get("method") not in _UNSAFE:
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        origin = headers.get(b"origin", b"").decode("latin-1").strip().lower()
        if not origin or origin in self.allowed:
            return await self.app(scope, receive, send)
        # The request's own host counts (same-origin, behind the /api rewrite).
        host = headers.get(b"x-forwarded-host") or headers.get(b"host") or b""
        if host and urlsplit(origin).netloc == host.decode("latin-1").lower():
            return await self.app(scope, receive, send)
        logger.warning("origin_check %s: foreign Origin %s on %s %s", self.mode, origin,
                       scope.get("method"), scope.get("path"))
        if self.mode == "enforce":
            return await _refuse(send, 403, "This request came from another site.")
        return await self.app(scope, receive, send)
