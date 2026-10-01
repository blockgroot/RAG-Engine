"""Push-triggered syncs: a provider says "something changed" and the connection
it is about gets `sync_requested_at` stamped, so the next tick syncs it instead
of waiting out the hourly poll (`jobs.autosync.request_sync_external`).

The Slack part matters beyond freshness: subscribing to channel messages means
Slack delivers EVERY message, and the events route used to answer any
non-DM `message`. A channel message must flag a sync and never be answered.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.api import slack_events
from app.db.connection import get_connection
from app.jobs.autosync import request_sync_external

from .conftest import requires_db

SLACK_SECRET = "test-slack-signing-secret"


@pytest.fixture
def slack_client(monkeypatch):
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SLACK_SECRET)
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    from app.api.main import create_app

    return TestClient(create_app())


def _slack_signed(body: dict, *, retry: str | None = None) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(
        SLACK_SECRET.encode(), b"v0:" + ts.encode() + b":" + raw, hashlib.sha256
    ).hexdigest()
    headers = {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}
    if retry:
        headers["X-Slack-Retry-Num"] = retry
    return raw, headers


def _slack_event(event: dict) -> dict:
    return {"type": "event_callback", "team_id": "T1", "event": event}


@pytest.fixture
def recorded(monkeypatch):
    flagged, answered = [], []
    monkeypatch.setattr(
        slack_events, "request_sync_external",
        lambda provider, team, **kw: flagged.append((provider, team, kw.get("slack_channel"))),
    )
    monkeypatch.setattr(slack_events, "_handle", lambda *a: answered.append(a))
    return flagged, answered


@pytest.mark.parametrize("channel_type", ["channel", "group"])
def test_a_channel_message_flags_a_sync_and_is_never_answered(slack_client, recorded, channel_type):
    flagged, answered = recorded
    raw, headers = _slack_signed(_slack_event(
        {"type": "message", "channel_type": channel_type, "channel": "C1", "user": "U1", "text": "deploy is frozen"}
    ))
    assert slack_client.post("/slack/events", content=raw, headers=headers).status_code == 200
    assert flagged == [("slack", "T1", "C1")]
    assert answered == []


def test_an_edit_is_a_content_change_too(slack_client, recorded):
    flagged, _ = recorded
    raw, headers = _slack_signed(_slack_event(
        # Slack's own example for an edit carries no `channel_type`.
        {"type": "message", "subtype": "message_changed", "channel": "C1"}
    ))
    slack_client.post("/slack/events", content=raw, headers=headers)
    assert flagged == [("slack", "T1", "C1")]


def test_a_retried_delivery_still_flags(slack_client, recorded):
    """The flag is idempotent, so a retry of a delivery that died on a cold
    start must still land -- unlike an answer, which a retry would duplicate."""
    flagged, _ = recorded
    raw, headers = _slack_signed(_slack_event(
        {"type": "message", "channel_type": "channel", "channel": "C1", "text": "x"}
    ), retry="1")
    slack_client.post("/slack/events", content=raw, headers=headers)
    assert flagged == [("slack", "T1", "C1")]


@pytest.mark.parametrize("event", [
    {"type": "message", "channel_type": "group", "channel": "C1", "bot_id": "B1", "text": "Searching…"},
    {"type": "message", "subtype": "message_changed", "channel": "C1",
     "message": {"bot_id": "B1", "text": "the answer"}},
    {"type": "message", "channel_type": "group", "channel": "C1", "user": "U1", "text": "<@UBOT> leave?"},
])
def test_the_bots_own_traffic_flags_nothing(slack_client, recorded, event):
    """Ingest never indexes it, so a sync for it would find nothing."""
    flagged, answered = recorded
    body = {**_slack_event(event), "authorizations": [{"user_id": "UBOT"}]}
    raw, headers = _slack_signed(body)
    assert slack_client.post("/slack/events", content=raw, headers=headers).status_code == 200
    assert flagged == [] and answered == []


def test_a_mention_and_a_dm_are_still_answered(slack_client, recorded):
    flagged, answered = recorded
    for event in (
        {"type": "app_mention", "channel": "C1", "user": "U1", "text": "<@B> leave policy?"},
        {"type": "message", "channel_type": "im", "channel": "D1", "user": "U1", "text": "leave policy?"},
    ):
        raw, headers = _slack_signed(_slack_event(event))
        slack_client.post("/slack/events", content=raw, headers=headers)
    assert len(answered) == 2
    assert flagged == []


# -- request_sync_external (real SQL) -----------------------------------------


@pytest.fixture
def org(store, org_cleanup):
    org_id = store.create_organization("Webhook Sync Test Org")
    org_cleanup.append(org_id)
    return org_id


def _connection(org_id, provider, external, *, config=None, reauth=False):
    with get_connection() as conn:
        return conn.execute(
            """
            INSERT INTO oauth_connections (org_id, provider, external_workspace_id,
                                           access_token_encrypted, source_config, needs_reauth)
            VALUES (%s, %s, %s, 'x', %s::jsonb, %s) RETURNING id::text
            """,
            (org_id, provider, external, json.dumps(config) if config else None, reauth),
        ).fetchone()[0]


def _flagged(connection_id) -> bool:
    with get_connection() as conn:
        return conn.execute(
            "SELECT sync_requested_at IS NOT NULL FROM oauth_connections WHERE id = %s",
            (connection_id,),
        ).fetchone()[0]


def _jobs(connection_id) -> int:
    with get_connection() as conn:
        return conn.execute(
            "SELECT count(*) FROM ingestion_jobs WHERE connection_id = %s", (connection_id,)
        ).fetchone()[0]


def _synced_minutes_ago(connection_id, minutes):
    with get_connection() as conn:
        conn.execute(
            "UPDATE oauth_connections SET last_sync_at = now() - make_interval(mins => %s) "
            "WHERE id = %s", (minutes, connection_id),
        )


@requires_db
def test_only_connections_indexing_the_channel_are_synced(org):
    team = f"T{uuid.uuid4().hex[:8]}"
    eng = _connection(org, "slack", team, config={"channel_ids": ["C-ENG"]})
    assert request_sync_external("slack", team, slack_channel="C-RANDOM") == 0
    assert _jobs(eng) == 0
    assert request_sync_external("slack", team, slack_channel="C-ENG") == 1
    assert _jobs(eng) == 1


@requires_db
def test_a_push_starts_the_sync_at_once_instead_of_waiting_for_the_tick(org):
    workspace = f"W{uuid.uuid4().hex[:8]}"
    conn_id = _connection(org, "notion", workspace)
    _synced_minutes_ago(conn_id, 60)
    request_sync_external("notion", workspace)
    assert _jobs(conn_id) == 1
    assert not _flagged(conn_id)  # sync_now cleared it: the work is queued


@requires_db
def test_a_burst_makes_one_job_and_leaves_the_rest_for_the_tick(org):
    """Fifty messages must not mean fifty syncs: while one is queued or
    running, further pushes only leave the flag for the next tick."""
    workspace = f"W{uuid.uuid4().hex[:8]}"
    conn_id = _connection(org, "notion", workspace)
    request_sync_external("notion", workspace)
    _synced_minutes_ago(conn_id, 60)  # cooled down, but a job is still active
    for _ in range(5):
        request_sync_external("notion", workspace)
    assert _jobs(conn_id) == 1
    assert _flagged(conn_id)


@requires_db
def test_inside_the_cooldown_the_change_waits_for_the_tick(org):
    """A busy channel must not run syncs back to back into Slack's rate limit."""
    workspace = f"W{uuid.uuid4().hex[:8]}"
    conn_id = _connection(org, "notion", workspace)
    _synced_minutes_ago(conn_id, 1)
    request_sync_external("notion", workspace)
    assert _jobs(conn_id) == 0
    assert _flagged(conn_id)


@requires_db
def test_an_expired_connection_is_not_flagged(org):
    workspace = f"W{uuid.uuid4().hex[:8]}"
    dead = _connection(org, "notion", workspace, reauth=True)
    assert request_sync_external("notion", workspace) == 0
    assert not _flagged(dead)


@requires_db
def test_an_empty_external_id_never_matches(org):
    _connection(org, "google", "")
    assert request_sync_external("google", "") == 0


# -- Notion / Linear / Drive receivers (app/api/webhooks.py) --------------------

from app.api import webhooks  # noqa: E402
from app.sources import drive_watch  # noqa: E402

NOTION_TOKEN = "secret_notion_verification"
LINEAR_SECRET = "lin_wh_test_secret"


@pytest.fixture
def hooks(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    for name in ("NOTION_WEBHOOK_VERIFICATION_TOKEN", "LINEAR_WEBHOOK_SECRET", "DRIVE_PUSH_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    flagged: list = []
    monkeypatch.setattr(webhooks, "request_sync_external", lambda p, e: flagged.append((p, e)))
    monkeypatch.setattr(webhooks, "request_sync_connection", lambda c: flagged.append(("conn", c)))
    from app.api.main import create_app

    return TestClient(create_app()), flagged


def _notion_sig(raw: bytes) -> str:
    return "sha256=" + hmac.new(NOTION_TOKEN.encode(), raw, hashlib.sha256).hexdigest()


def test_notion_handshake_is_accepted_before_any_secret_exists(hooks, caplog):
    client, flagged = hooks
    response = client.post("/webhooks/notion", json={"verification_token": NOTION_TOKEN})
    assert response.status_code == 200
    assert NOTION_TOKEN in caplog.text  # the operator copies it from here
    assert flagged == []


def test_notion_events_are_closed_until_the_token_is_set(hooks):
    client, _ = hooks
    raw = json.dumps({"type": "page.content_updated", "workspace_id": "W1"}).encode()
    assert client.post("/webhooks/notion", content=raw, headers={"X-Notion-Signature": _notion_sig(raw)}).status_code == 404


def test_notion_rejects_a_bad_signature(hooks, monkeypatch):
    client, flagged = hooks
    monkeypatch.setenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", NOTION_TOKEN)
    raw = json.dumps({"type": "page.content_updated", "workspace_id": "W1"}).encode()
    assert client.post("/webhooks/notion", content=raw, headers={"X-Notion-Signature": "sha256=00"}).status_code == 401
    assert flagged == []


def test_a_signed_notion_event_flags_its_workspace(hooks, monkeypatch):
    client, flagged = hooks
    monkeypatch.setenv("NOTION_WEBHOOK_VERIFICATION_TOKEN", NOTION_TOKEN)
    raw = json.dumps({"type": "page.content_updated", "workspace_id": "W1", "entity": {"id": "p"}}).encode()
    assert client.post("/webhooks/notion", content=raw, headers={"X-Notion-Signature": _notion_sig(raw)}).status_code == 200
    assert flagged == [("notion", "W1")]


def _linear(body: dict) -> tuple[bytes, dict]:
    raw = json.dumps(body).encode()
    return raw, {"Linear-Signature": hmac.new(LINEAR_SECRET.encode(), raw, hashlib.sha256).hexdigest()}


def test_linear_is_closed_until_the_secret_is_set(hooks):
    client, _ = hooks
    raw, headers = _linear({"organizationId": "O1", "webhookTimestamp": int(time.time() * 1000)})
    assert client.post("/webhooks/linear", content=raw, headers=headers).status_code == 404


def test_a_signed_fresh_linear_event_flags_its_organization(hooks, monkeypatch):
    client, flagged = hooks
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", LINEAR_SECRET)
    raw, headers = _linear({"action": "update", "type": "Issue", "organizationId": "O1",
                            "webhookTimestamp": int(time.time() * 1000)})
    assert client.post("/webhooks/linear", content=raw, headers=headers).status_code == 200
    assert flagged == [("linear", "O1")]


def test_a_replayed_linear_delivery_is_refused(hooks, monkeypatch):
    client, flagged = hooks
    monkeypatch.setenv("LINEAR_WEBHOOK_SECRET", LINEAR_SECRET)
    raw, headers = _linear({"organizationId": "O1", "webhookTimestamp": int(time.time() * 1000) - 120_000})
    assert client.post("/webhooks/linear", content=raw, headers=headers).status_code == 401
    raw, _ = _linear({"organizationId": "O1", "webhookTimestamp": int(time.time() * 1000)})
    assert client.post("/webhooks/linear", content=raw, headers={"Linear-Signature": "00"}).status_code == 401
    assert flagged == []


def test_drive_is_closed_unless_push_is_configured(hooks):
    client, _ = hooks
    assert client.post("/webhooks/google", headers={"X-Goog-Resource-State": "change"}).status_code == 404


@pytest.mark.parametrize("state", ["change", "changed", "update"])
def test_a_proven_drive_notification_flags_its_connection(hooks, monkeypatch, state):
    client, flagged = hooks
    monkeypatch.setenv("DRIVE_PUSH_BASE_URL", "https://api.example.com")
    monkeypatch.setattr(
        drive_watch, "connection_for_notification",
        lambda cid, tok: "conn-1" if (cid, tok) == ("ch-1", "good") else None,
    )
    for token in ("good", "forged"):
        response = client.post("/webhooks/google", headers={
            "X-Goog-Channel-ID": "ch-1", "X-Goog-Channel-Token": token,
            "X-Goog-Resource-State": state,
        })
        assert response.status_code == 200  # never make Google retry
    assert flagged == [("conn", "conn-1")]


def test_the_drive_sync_handshake_is_not_a_change(hooks, monkeypatch):
    client, flagged = hooks
    monkeypatch.setenv("DRIVE_PUSH_BASE_URL", "https://api.example.com")
    monkeypatch.setattr(drive_watch, "connection_for_notification", lambda *a: "conn-1")
    client.post("/webhooks/google", headers={"X-Goog-Channel-ID": "ch-1", "X-Goog-Resource-State": "sync"})
    assert flagged == []


# -- drive_watch channel lifecycle (real SQL, fake Google) ----------------------


class _GoogleResp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._body


@pytest.fixture
def fake_google(monkeypatch):
    calls: list = []

    def get(url, **kw):
        calls.append(("get", url))
        return _GoogleResp({"startPageToken": "42"})

    def post(url, **kw):
        calls.append(("post", url, kw.get("json"), kw.get("params")))
        if url.endswith("/changes/watch"):
            return _GoogleResp({"resourceId": f"res-{len(calls)}", "expiration": str(int((time.time() + 604800) * 1000))})
        return _GoogleResp({})

    monkeypatch.setattr(drive_watch.httpx, "get", get)
    monkeypatch.setattr(drive_watch.httpx, "post", post)
    monkeypatch.setattr("app.auth.credentials.get_live_connection_token", lambda *a, **k: "tok")
    return calls


@requires_db
def test_a_scoped_drive_connection_gets_a_channel_and_a_notification_proves_it(org, fake_google):
    conn_id = _connection(org, "google", f"g{uuid.uuid4().hex[:6]}", config={"folder_id": "F1"})
    settings = drive_watch.WebhookSettings(drive_push_base_url="https://api.example.com")
    assert drive_watch.ensure_watches(settings) >= 1

    [watch] = [c for c in fake_google if c[0] == "post" and c[1].endswith("/changes/watch")]
    body, params = watch[2], watch[3]
    assert body["address"] == "https://api.example.com/webhooks/google"
    assert body["type"] == "web_hook" and params["pageToken"] == "42"

    assert drive_watch.connection_for_notification(body["id"], body["token"]) == conn_id
    assert drive_watch.connection_for_notification(body["id"], "forged") is None
    # Not due again until it nears expiry.
    fake_google.clear()
    drive_watch.ensure_watches(settings)
    assert not [c for c in fake_google if c[1].endswith("/changes/watch")]


@requires_db
def test_an_expiring_channel_is_replaced_and_the_old_one_stopped(org, fake_google):
    conn_id = _connection(org, "google", f"g{uuid.uuid4().hex[:6]}", config={"folder_id": "F1"})
    settings = drive_watch.WebhookSettings(drive_push_base_url="https://api.example.com")
    drive_watch.ensure_watches(settings)
    with get_connection() as conn:
        old = conn.execute(
            "UPDATE drive_watch_channels SET expires_at = now() + interval '1 hour' "
            "WHERE connection_id = %s RETURNING channel_id, resource_id", (conn_id,),
        ).fetchone()
    fake_google.clear()
    drive_watch.ensure_watches(settings)
    stops = [c for c in fake_google if c[1].endswith("/channels/stop")]
    assert stops and stops[0][2] == {"id": old[0], "resourceId": old[1]}
    with get_connection() as conn:
        new = conn.execute(
            "SELECT channel_id FROM drive_watch_channels WHERE connection_id = %s", (conn_id,)
        ).fetchone()[0]
    assert new != old[0]


@requires_db
def test_an_unscoped_drive_connection_is_not_watched(org, fake_google):
    _connection(org, "google", f"g{uuid.uuid4().hex[:6]}")
    drive_watch.ensure_watches(drive_watch.WebhookSettings(drive_push_base_url="https://api.example.com"))
    assert not [c for c in fake_google if c[1].endswith("/changes/watch")]


def test_nothing_is_watched_without_a_public_url(fake_google):
    assert drive_watch.ensure_watches(drive_watch.WebhookSettings()) == 0
    assert fake_google == []
