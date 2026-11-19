"""Agent-mode outcomes from the production-readiness audit: a write whose line
broke after connecting is "unknown" (never re-approved into a duplicate), and a
confirm re-checks space membership."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.actions import mcp_client, service
from app.core.exceptions import ActionError, ActionOutcomeUnknown, ActionRejected, AuthError


def _session():
    s = object.__new__(mcp_client.McpSession)
    s.url, s.token, s.timeout, s.session_id, s._next = "https://tool.example", None, 1.0, None, 0
    return s


@pytest.mark.parametrize("error, write, expected", [
    (httpx.ReadError("reset"), True, ActionOutcomeUnknown),
    (httpx.RemoteProtocolError("eof"), True, ActionOutcomeUnknown),
    (httpx.ConnectError("refused"), True, ActionError),
    (httpx.ReadError("reset"), False, ActionError),
])
def test_a_broken_line_after_sending_is_unknown(monkeypatch, error, write, expected):
    def boom(*a, **k):
        raise error
    monkeypatch.setattr(mcp_client.httpx, "post", boom)
    with pytest.raises(expected) as caught:
        _session()._send("tools/call", {}, write=write)
    if expected is ActionError:
        assert not isinstance(caught.value, ActionOutcomeUnknown)


def test_confirm_refuses_a_removed_space_member(monkeypatch):
    monkeypatch.setattr(service.policy, "act_on", lambda org: True)
    monkeypatch.setattr(service.store, "get",
                        lambda *a: SimpleNamespace(workspace_id="ws1", action="x"))

    def not_member(*a):
        raise AuthError("Not a member of this workspace")
    monkeypatch.setattr("app.workspaces.store.assert_member", not_member)
    claimed = []
    monkeypatch.setattr(service.store, "claim", lambda *a, **k: claimed.append(1))
    with pytest.raises(ActionRejected, match="no longer a member"):
        service.confirm(org_id="o", user_id="u", action_id="a", shown_hash="h",
                        acknowledged=False, settings=SimpleNamespace())
    assert not claimed
