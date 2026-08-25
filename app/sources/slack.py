"""Slack implementation of the ``SourceAdapter`` interface."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

import httpx

from ..config.settings import SlackSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import SourceAdapter, SourceDocument, SourceRef

_API_BASE = "https://slack.com/api"
_TIMEOUT = 15.0
_TRUNCATION_MARKER = "\n[... earlier replies truncated: thread exceeds ingest size limit ...]"
_MAX_HTTP_ATTEMPTS = 5
_RETRYABLE_SLACK_ERRORS = frozenset({"ratelimited", "internal_error", "fatal_error"})
# ponytail: process-local only; a separate worker process still lists itself.
# Upgrade: persist refs on the ingestion job row.
_LISTING_CACHE_TTL_SECONDS = 180
_LISTING_CACHE: dict[tuple, tuple[float, list[SourceRef]]] = {}


def _plain_text(text: str) -> str:
    """Slack mrkdwn -> readable text.

    Slack escapes ``&``/``<``/``>`` in message text and wraps links as
    ``<https://x|label>``, so raw ``text`` renders as "Vector &amp;
    Relational" and dumps full URLs mid-sentence. Both were visible in a real
    report before this: the escaping is not cosmetic, it is the difference
    between a paragraph and noise.

    Deliberately minimal — this unescapes and unwraps links; it does not try
    to strip ``*bold*``/``_italic_`` markers, which read fine as-is and whose
    removal risks eating literal asterisks from a code snippet.
    """
    import html as _html
    import re as _re

    text = _re.sub(r"<(?:https?://[^|>]+)\|([^>]+)>", r"\1", text)  # <url|label>
    text = _re.sub(r"<(https?://[^>]+)>", r"\1", text)               # <url>
    text = _re.sub(r"<[@#]([^|>]+)(?:\|([^>]+))?>", lambda m: f"@{m.group(2) or m.group(1)}", text)
    return _html.unescape(text)


def _ts_to_dt(ts: str | None) -> datetime | None:
    """Slack timestamps are ``"1234567890.123456"`` (seconds, as a string)."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _thread_uri(channel_id: str, thread_ts: str) -> str:
    padded = f"{thread_ts:0<17}".replace(".", "")
    return f"https://slack.com/archives/{channel_id}/p{padded}"


def _retry_after_seconds(response: httpx.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return min(2 ** attempt, 30)


def clear_listing_cache() -> None:
    """Drop cached Slack listings. Tests only — production never needs this."""
    _LISTING_CACHE.clear()


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
        self._channel_names = dict(channel_names or {})
        self._timeout = timeout
        self._settings = settings or SlackSettings.from_env()
        self._user_names: dict[str, str] = {}

    def _channel_label(self, channel_id: str) -> str:
        return self._channel_names.get(channel_id) or channel_id

    def _thread_title(self, channel_id: str, text: str) -> str:
        channel = self._channel_label(channel_id)
        snippet = (text or "").strip()[:70]
        return f"#{channel}: {snippet}" if snippet else f"Thread in #{channel}"

    def _listing_cache_key(self) -> tuple:
        digest = hashlib.sha256(self._token.encode()).hexdigest()[:16]
        return (
            digest,
            tuple(self._channel_ids),
            self._settings.backfill_days,
            self._settings.min_thread_chars,
            self._settings.max_documents_per_sync,
        )

    def _cached_listing(self) -> list[SourceRef] | None:
        hit = _LISTING_CACHE.get(self._listing_cache_key())
        if hit is None:
            return None
        saved_at, refs = hit
        if time.time() - saved_at > _LISTING_CACHE_TTL_SECONDS:
            _LISTING_CACHE.pop(self._listing_cache_key(), None)
            return None
        return list(refs)

    def _store_listing(self, refs: list[SourceRef]) -> None:
        if refs:
            _LISTING_CACHE[self._listing_cache_key()] = (time.time(), list(refs))

    def _get(self, method: str, params: dict) -> dict:
        last_error = "unknown error"
        for attempt in range(1, _MAX_HTTP_ATTEMPTS + 1):
            try:
                response = httpx.get(
                    f"{_API_BASE}/{method}",
                    params=params,
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                last_error = str(exc)
                if attempt >= _MAX_HTTP_ATTEMPTS:
                    raise SourceError(
                        f"Slack API call to {method} failed: {exc}", cause=exc
                    ) from exc
                time.sleep(min(2 ** attempt, 30))
                continue

            if response.status_code == 429:
                last_error = "ratelimited"
                if attempt >= _MAX_HTTP_ATTEMPTS:
                    raise SourceError(f"Slack API call to {method} failed: ratelimited")
                time.sleep(min(_retry_after_seconds(response, attempt), 8))
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPError as exc:
                last_error = str(exc)
                if attempt >= _MAX_HTTP_ATTEMPTS:
                    raise SourceError(
                        f"Slack API call to {method} failed: {exc}", cause=exc
                    ) from exc
                time.sleep(min(2 ** attempt, 30))
                continue

            data = response.json()
            if data.get("ok"):
                return data
            err = str(data.get("error") or "unknown")
            last_error = err
            if err in _RETRYABLE_SLACK_ERRORS and attempt < _MAX_HTTP_ATTEMPTS:
                time.sleep(min(_retry_after_seconds(response, attempt), 8))
                continue
            raise SourceError(f"Slack API call to {method} failed: {err}")
        raise SourceError(f"Slack API call to {method} failed: {last_error}")

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
        try:
            refs = self._list_documents_from_slack()
        except SourceError:
            cached = self._cached_listing()
            if cached:
                return cached
            raise
        if refs:
            self._store_listing(refs)
            return refs
        return self._cached_listing() or refs

    def _list_documents_from_slack(self) -> list[SourceRef]:
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
                        continue
                    reply_count = message.get("reply_count", 0)
                    text = (message.get("text") or "").strip()
                    if reply_count == 0 and len(text) < self._settings.min_thread_chars:
                        continue
                    last_ts = message.get("latest_reply") or ts
                    refs.append(
                        SourceRef(
                            external_id=f"{channel_id}:{ts}",
                            title=self._thread_title(channel_id, text),
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

    def channel_labels(self) -> list[str]:
        """The channels this adapter reads, by name where known.

        Exposed so an activity report can state its own coverage. A Slack
        connection only ever sees the channels an admin picked and the bot was
        invited to, so a report that stayed silent about that would imply
        whole-workspace coverage it never had.
        """
        return [self._channel_label(cid) for cid in self._channel_ids]

    def fetch_recent_messages(
        self, since: float, *, max_messages: int = 300
    ) -> tuple[list[dict], list[str]]:
        """Messages posted since ``since`` (unix seconds), as an activity feed.

        Returns ``(messages, truncated_channels)`` — the caller needs the
        second element to disclose partial coverage, since a report that read
        half of #general while claiming to have checked it is the failure that
        matters here.

        Deliberately NOT ``list_documents``: that returns thread *refs* for the
        ingestion pipeline to chunk and embed. The Prompt-Driven Activity
        Scheduler wants the actual recent messages to hand an LLM, and stores
        nothing — so it needs the raw feed, not documents.

        Reuses the same ``conversations.history`` + ``oldest`` call the listing
        already makes; the only real difference is that ``since`` comes from the
        caller (a scheduler's last run) instead of a fixed rolling window, and
        no ``min_thread_chars``/thread filtering applies — a one-line message is
        still activity worth reporting on.

        ``max_messages`` is split **per channel** rather than spent greedily in
        channel order: one busy channel would otherwise consume the whole
        budget and every later channel would silently contribute nothing while
        ``channel_labels()`` still claimed it was checked. Slack returns
        history newest-first, so what a per-channel cap drops is always the
        oldest end of the window.
        """
        channel_ids = list(self._channel_ids)
        if not channel_ids:
            return [], []
        per_channel = max(1, max_messages // len(channel_ids))

        collected: list[dict] = []
        truncated: list[str] = []
        for channel_id in channel_ids:
            kept = 0
            cursor = None
            while True:
                params = {"channel": channel_id, "oldest": since, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                data = self._get("conversations.history", params)
                for message in data.get("messages", []):
                    ts = message.get("ts")
                    text = _plain_text((message.get("text") or "").strip())
                    # Skip join/leave/etc. system events and empty posts: they
                    # are noise in a report, not activity.
                    if not ts or message.get("subtype") or not text:
                        continue
                    if kept >= per_channel:
                        truncated.append(self._channel_label(channel_id))
                        break
                    collected.append(
                        {
                            "channel": self._channel_label(channel_id),
                            "channel_id": channel_id,
                            "user": self._display_name(message.get("user")),
                            "text": text,
                            "at": _ts_to_dt(ts),
                            "reply_count": message.get("reply_count", 0),
                            # Built from channel_id + ts rather than fetched:
                            # chat.getPermalink would cost one API call PER
                            # message, and this is the same URL Slack returns.
                            "permalink": _thread_uri(channel_id, ts),
                        }
                    )
                    kept += 1
                else:
                    cursor = (data.get("response_metadata") or {}).get("next_cursor")
                    if cursor:
                        continue
                break
        return collected, truncated

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

        root_text = (messages[0].get("text") or "").strip()
        channel = self._channel_label(channel_id)
        lines = [f"Channel: #{channel}"]
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
        title = self._thread_title(channel_id, root_text)

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
