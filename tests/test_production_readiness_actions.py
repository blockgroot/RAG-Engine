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


def test_a_space_digest_stops_when_its_owner_leaves(monkeypatch):
    """Raised before any read, so a removed member's report holds nothing."""
    from app.schedulers import runner

    monkeypatch.setattr(runner, "get_user", lambda uid: SimpleNamespace(email="a@x.com"))

    def not_member(*a):
        raise AuthError("Not a member of this workspace")
    monkeypatch.setattr(runner, "assert_member", not_member)
    read = []
    monkeypatch.setattr(runner, "window_start", lambda s: read.append(1))
    scheduler = SimpleNamespace(id="s", user_id="u", org_id="o", workspace_id="ws")
    with pytest.raises(Exception, match="no longer a member"):
        runner.run_scheduler_once(scheduler)
    assert not read


def test_a_window_already_emailed_is_not_sent_again(monkeypatch):
    """A crash between the email and recording success sent the report twice."""
    from datetime import datetime, timezone

    from app.schedulers import runner, worker

    sent_until = datetime(2026, 11, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(runner, "get_user", lambda uid: SimpleNamespace(email="a@x.com"))
    monkeypatch.setattr(runner.reports, "delivered_window_end", lambda sid, start: sent_until)
    fetched = []
    monkeypatch.setattr(runner, "fetch_activity", lambda *a, **k: fetched.append(1))
    scheduler = SimpleNamespace(id="s", user_id="u", org_id="o", workspace_id=None,
                                frequency="daily", provider="slack",
                                last_run_at=datetime(2026, 11, 17, tzinfo=timezone.utc))
    monkeypatch.setattr(runner, "window_start", lambda s: s.last_run_at)
    monkeypatch.setattr(worker.scheduler_queue, "requeue_interrupted_running", lambda **k: 0)
    monkeypatch.setattr(worker.scheduler_queue, "claim_due", lambda limit: [scheduler])
    recorded = []
    monkeypatch.setattr(worker.scheduler_queue, "mark_run_success",
                        lambda sid, freq, covered_until=None: recorded.append(covered_until))
    monkeypatch.setattr(worker.scheduler_queue, "mark_run_failed",
                        lambda *a, **k: recorded.append("failed"))

    assert worker.run_due_schedulers_once(SimpleNamespace(enabled=True, batch_size=5,
                                                          max_attempts=3)) == 1
    assert recorded == [sent_until]
    assert not fetched
