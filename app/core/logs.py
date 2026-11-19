"""Process logging and the request id every log line carries.

Without this the root logger had no handler: uvicorn configures only its own
loggers, so every ``logger.info`` here (LLM usage, requeues, actions run) was
dropped, and warnings printed bare, with no time or logger name.
"""

from __future__ import annotations

import contextvars
import logging
import re
import uuid

#: This request's id ("-" outside a request: the worker, startup). A context
#: variable, so worker threads a request starts carry it too.
REQUEST_ID: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_FORMAT = "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"
#: An incoming id is kept only if it is this plain: it is written into logs.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Give the root logger one stream handler, once. A handler already there
    (tests, an embedding host) is left alone apart from the request id."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in root.handlers:
        if not any(isinstance(f, _RequestIdFilter) for f in handler.filters):
            handler.addFilter(_RequestIdFilter())


class RequestIdMiddleware:
    """Pure ASGI (``BaseHTTPMiddleware`` changes how a streamed answer is
    cancelled): reuse a sane ``X-Request-ID`` or make one, set it for the
    request's logs, and send it back so a user's report can be found."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex[:16]
        token = REQUEST_ID.set(request_id)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"],
                                      (b"x-request-id", request_id.encode("latin-1"))]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            REQUEST_ID.reset(token)
