"""Slack: re-read one thread that retrieval already returned (gated on D10).

Not in the default ``LIVE_TOOLS_PROVIDERS``: non-Marketplace apps get
``conversations.replies`` at ~1 request/min and 15 messages per call, and a
live read that takes a minute is not live. Enable it only once the Phase 0
tier check says the app is on the non-restricted tier. Asks for at most
``MAX_MESSAGES`` either way, and says so when there are more.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from . import base
from .base import ProviderRead

_API = "https://slack.com/api/conversations.replies"
MAX_MESSAGES = 15

_GONE = {"channel_not_found", "not_in_channel", "thread_not_found", "is_archived",
         "access_denied", "missing_scope"}
_AUTH = {"invalid_auth", "token_revoked", "token_expired", "account_inactive", "not_authed"}


def read_thread(token: str, external_id: str) -> ProviderRead:
    channel, _, thread_ts = external_id.partition(":")
    if not channel or not thread_ts:
        return ProviderRead(base.ERROR, reason="bad_external_id")
    try:
        response = httpx.get(
            _API, params={"channel": channel, "ts": thread_ts, "limit": MAX_MESSAGES},
            headers={"Authorization": f"Bearer {token}"}, timeout=base.TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        return ProviderRead(base.TIMEOUT)
    except httpx.HTTPError as exc:
        return ProviderRead(base.ERROR, reason=type(exc).__name__)
    if response.status_code == 429:
        return ProviderRead(base.RATE_LIMITED, reason="http_429")
    try:
        data = response.json()
    except ValueError:
        return ProviderRead(base.ERROR, reason=f"http_{response.status_code}")
    if not data.get("ok"):
        error = str(data.get("error") or "unknown")
        if error == "ratelimited":
            return ProviderRead(base.RATE_LIMITED, reason=error)
        if error in _AUTH:
            return ProviderRead(base.REAUTH, reason=error)
        if error in _GONE:
            return ProviderRead(base.NOT_ACCESSIBLE, reason=error)
        return ProviderRead(base.ERROR, reason=error)

    messages = [m for m in data.get("messages") or [] if not m.get("bot_id")]
    if not messages:
        return ProviderRead(base.NOT_ACCESSIBLE, reason="empty_thread")
    lines = []
    for m in messages[:MAX_MESSAGES]:
        who = ((m.get("user_profile") or {}).get("real_name")
               or (m.get("user_profile") or {}).get("display_name") or "A member")
        when = _when(m.get("ts"))
        lines.append(f"{who}{f' ({when})' if when else ''}: {(m.get('text') or '').strip()}")
    if data.get("has_more"):
        lines.append(f"(Only the first {MAX_MESSAGES} messages of this thread are shown.)")
    return ProviderRead(base.OK, text="Slack thread\n\n" + "\n".join(lines))


def _when(ts: str | None) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%d %b %Y %H:%M UTC")
    except (TypeError, ValueError):
        return ""
