"""Slack implementation of the ``SourceAdapter`` interface.

Phase 2 of the Slack Integration Plan (docs/plans/2026-08-17-slack-integration.md).
Structurally mirrors ``google_drive.py``/``notion.py`` — same class shape, same
``SourceError``/``ConfigurationError`` wrapping with ``cause=`` — over the plain
Slack Web API via ``httpx`` (no vendor SDK; same "thin HTTP client, not a
framework" reasoning as every other adapter here).

**One Slack thread = one document** (decision D3): a lone reply is usually
meaningless out of context, and a whole channel-day would blur unrelated
conversations together, so the thread is the natural unit — same conclusion
Onyx's community connector reached independently (see the plan's §0 research).
A message with no replies is simply a one-message "thread."
``external_id`` is ``"{channel_id}:{thread_ts}"``.

**No persistent checkpoint** (a deliberate simplification vs. the plan's D4
language): neither ``NotionAdapter`` nor ``GoogleDriveAdapter`` persist a
walk checkpoint either — they just bound what one ``list_documents()`` call
can return (Drive: ``max_walk_folders``/``max_documents``; Notion: page-tree
size) and re-walk that bounded window every call. Slack follows the same
existing convention rather than inventing new adapter-level state: every
``list_documents()`` call re-walks the last ``backfill_days`` of each
configured channel, bounded by ``max_documents_per_sync`` in aggregate.

**Volume bounds** (plan §6), all from ``SlackSettings``, applied together:
``backfill_days`` bounds history depth, ``min_thread_chars`` filters
low-signal lone messages before they become a document, ``max_thread_messages``
caps one thread's rendered size (truncation marker, never silent),
``max_documents_per_sync`` caps the aggregate across one call. Config is
required to name specific channels (``source_config["channel_ids"]``) — never
"every channel the bot can see" (decision D2).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import httpx

from ..config.settings import SlackSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import SourceAdapter, SourceDocument, SourceRef

_API_BASE = "https://slack.com/api"
_TIMEOUT = 15.0
_TRUNCATION_MARKER = "\n[... earlier replies truncated: thread exceeds ingest size limit ...]"


def _ts_to_dt(ts: str | None) -> datetime | None:
    """Slack timestamps are ``"1234567890.123456"`` (seconds, as a string)."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _thread_uri(channel_id: str, thread_ts: str) -> str:
    # Slack's deep-link format drops the "." and zero-pads to 6 decimal places.
    padded = f"{thread_ts:0<17}".replace(".", "")
    return f"https://slack.com/archives/{channel_id}/p{padded}"


class SlackAdapter(SourceAdapter):
    """Fetches messages from a fixed set of Slack channels, as one document per thread."""

    def __init__(
        self,
        token: str,
        channel_ids: list[str],
        *,
        channel_names: dict[str, str] | None = None,
        timeout: float = _TIMEOUT,
        settings: SlackSettings | None = None,
    ) -> None:
        if not token:
            raise ConfigurationError("SlackAdapter requires a non-empty access token")
        if not channel_ids:
            raise ConfigurationError(
                "SlackAdapter requires at least one channel_id (config['channel_ids'])"
            )
        self._token = token
        self._channel_ids = list(channel_ids)
        # Human channel name (config["channel_names"], the same map the Sources
        # UI already stores) prefixed onto each thread's title. Without it a
        # thread's "title" is just the first 80 chars of a message, which is
        # both a bad citation label and — the reason this was added — leaves
        # the Slack recap prompt no way to confirm a thread came from the
        # channel a question names, so it declines rather than guess.
        self._channel_names = dict(channel_names or {})
        self._timeout = timeout
        self._settings = settings or SlackSettings.from_env()
        self._user_names: dict[str, str] = {}

    def _channel_label(self, channel_id: str) -> str:
        return self._channel_names.get(channel_id) or channel_id

    def _get(self, method: str, params: dict) -> dict:
        try:
            response = httpx.get(
                f"{_API_BASE}/{method}",
                params=params,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(f"Slack API call to {method} failed: {exc}", cause=exc) from exc

        data = response.json()
        if not data.get("ok"):
            # Slack's failure shape: HTTP 200 with {"ok": false, "error": "..."}.
            raise SourceError(f"Slack API call to {method} failed: {data.get('error')}")
        return data

    def _display_name(self, user_id: str | None) -> str:
        if not user_id:
            return "unknown"
        cached = self._user_names.get(user_id)
        if cached:
            return cached
        try:
            data = self._get("users.info", {"user": user_id})
            profile = data.get("user", {})
            name = (
                profile.get("profile", {}).get("display_name")
                or profile.get("real_name")
                or profile.get("name")
                or user_id
            )
        except SourceError:
            name = user_id  # Best-effort: a bad lookup shouldn't fail the whole sync.
        self._user_names[user_id] = name
        return name

    def list_documents(self) -> list[SourceRef]:
        oldest = time.time() - (self._settings.backfill_days * 86400)
        refs: list[SourceRef] = []
        for channel_id in self._channel_ids:
            cursor = None
            while True:
                if len(refs) >= self._settings.max_documents_per_sync:
                    return refs
                params = {"channel": channel_id, "oldest": oldest, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                data = self._get("conversations.history", params)
                for message in data.get("messages", []):
                    ts = message.get("ts")
                    if not ts or message.get("subtype"):
                        continue  # skip join/leave/edit system messages
                    reply_count = message.get("reply_count", 0)
                    text = (message.get("text") or "").strip()
                    if reply_count == 0 and len(text) < self._settings.min_thread_chars:
                        continue  # noise filter: a lone short message, plan §6.2
                    last_ts = message.get("latest_reply") or ts
                    refs.append(
                        SourceRef(
                            external_id=f"{channel_id}:{ts}",
                            title=f"#{self._channel_label(channel_id)}: {text[:70]}"
                            if text
                            else f"Thread in #{self._channel_label(channel_id)}",
                            last_modified=_ts_to_dt(last_ts),
                            source_uri=_thread_uri(channel_id, ts),
                        )
                    )
                    if len(refs) >= self._settings.max_documents_per_sync:
                        return refs
                cursor = (data.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
        return refs

    def _fetch_thread_messages(self, channel_id: str, thread_ts: str) -> tuple[list[dict], bool]:
        """Return (messages, truncated) for one thread, newest-kept if oversized."""
        messages: list[dict] = []
        cursor = None
        while True:
            params = {"channel": channel_id, "ts": thread_ts, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            data = self._get("conversations.replies", params)
            messages.extend(data.get("messages", []))
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        truncated = len(messages) > self._settings.max_thread_messages
        if truncated:
            # Keep the root message plus the most recent N-1 replies — the
            # start of a conversation is as load-bearing as its tail, but the
            # tail is what "what's the latest" questions actually need.
            root = messages[0]
            tail = messages[1:][-(self._settings.max_thread_messages - 1):]
            messages = [root, *tail]
        return messages, truncated

    def fetch_document(self, external_id: str) -> SourceDocument:
        channel_id, _, thread_ts = external_id.partition(":")
        if not channel_id or not thread_ts:
            raise SourceError(f"Malformed Slack external_id: {external_id!r}")

        messages, truncated = self._fetch_thread_messages(channel_id, thread_ts)
        if not messages:
            raise SourceError(f"Slack thread {external_id} has no messages")

        lines = []
        if truncated:
            lines.append(_TRUNCATION_MARKER.strip())
        for message in messages:
            ts = message.get("ts")
            when = _ts_to_dt(ts)
            stamp = when.strftime("%H:%M") if when else "??:??"
            name = self._display_name(message.get("user"))
            text = (message.get("text") or "").strip()
            lines.append(f"[{stamp}] {name}: {text}")

        content = "\n".join(lines)
        last_modified = _ts_to_dt(messages[-1].get("ts"))
        title = (messages[0].get("text") or "").strip()[:80] or f"Thread in {channel_id}"

        return SourceDocument(
            external_id=external_id,
            title=title,
            content=content,
            source_uri=_thread_uri(channel_id, thread_ts),
            last_modified=last_modified,
        )

    def get_last_modified(self, external_id: str) -> datetime | None:
        channel_id, _, thread_ts = external_id.partition(":")
        if not channel_id or not thread_ts:
            raise SourceError(f"Malformed Slack external_id: {external_id!r}")
        messages, _ = self._fetch_thread_messages(channel_id, thread_ts)
        if not messages:
            return None
        return _ts_to_dt(messages[-1].get("ts"))
