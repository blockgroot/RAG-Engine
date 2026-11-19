"""A refresh that failed on the network is not a revoked grant: only a definitive
refusal flags the connection for reconnect (which stops autosync for good)."""

from __future__ import annotations

import httpx
import pytest

from app.auth.credentials import _transient
from app.core.exceptions import OAuthError


def _status(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://oauth.example/token")
    return httpx.HTTPStatusError("x", request=request,
                                 response=httpx.Response(code, request=request))


def _wrapped(inner: BaseException) -> OAuthError:
    try:
        raise OAuthError("refresh failed", cause=inner) from inner
    except OAuthError as exc:
        return exc


@pytest.mark.parametrize("inner, transient", [
    (httpx.ReadTimeout("slow"), True),
    (httpx.ConnectError("refused"), True),
    (_status(503), True),
    (_status(429), True),
    (_status(400), False),
    (_status(401), False),
])
def test_only_network_and_provider_failures_are_transient(inner, transient):
    assert _transient(_wrapped(inner)) is transient


def test_a_failure_with_no_http_cause_needs_a_reconnect():
    assert _transient(OAuthError("refresh response missing access_token")) is False
