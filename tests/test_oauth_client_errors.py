"""A refresh refused because of THIS APP's OAuth client (invalid_client) must
not mark the company's connection "Reconnect": reconnecting cannot fix it, and
the flag took the source out of routing (staging, sales e2e, 3 Nov 2026)."""

from __future__ import annotations

import httpx
import pytest

from app.auth.credentials import looks_like_auth_failure
from app.core.exceptions import ConfigurationError, OAuthError, OAuthReauthRequiredError


def _refused(status: int, error: str):
    request = httpx.Request("POST", "https://oauth.test/token")
    response = httpx.Response(status, json={"error": error}, request=request)
    return httpx.HTTPStatusError(f"{status} Unauthorized", request=request, response=response)


@pytest.mark.parametrize("module, cls", [("app.auth.google_oauth", "GoogleOAuthProvider"),
                                         ("app.auth.linear_oauth", "LinearOAuthProvider")])
def test_a_refused_client_is_configuration_and_a_refused_grant_is_reauth(monkeypatch, module, cls):
    import importlib

    mod = importlib.import_module(module)
    provider = object.__new__(getattr(mod, cls))
    provider._settings = type("S", (), {"client_id": "id", "client_secret": "secret"})()

    def post(error, status):
        def _post(*a, **k):
            raise _refused(status, error)
        return _post

    monkeypatch.setattr(mod.httpx, "post", post("invalid_client", 401))
    with pytest.raises(ConfigurationError) as caught:
        provider.refresh("token")
    assert not looks_like_auth_failure(caught.value)

    monkeypatch.setattr(mod.httpx, "post", post("invalid_grant", 400))
    with pytest.raises((OAuthError, OAuthReauthRequiredError)):
        provider.refresh("token")
