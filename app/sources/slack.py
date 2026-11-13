"""Slack implementation of the ``SourceAdapter`` interface."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone

import httpx

from ..config.settings import SlackSettings
from ..core.exceptions import ConfigurationError, SourceError
from .base import DocAccess, SourceAdapter, SourceDocument, SourceRef
from .meta import build_meta, container, link, person
from .slack_utils import channel_tag, list_channel_members, list_slack_channels

logger = logging.getLogger(__name__)

_API_BASE = "https://slack.com/api"
_TIMEOUT = 15.0
_TRUNCATION_MARKER = "\n[... earlier replies truncated: thread exceeds ingest size limit ...]"
_MAX_HTTP_ATTEMPTS = 5
_RETRYABLE_SLACK_ERRORS = frozenset({"ratelimited", "internal_error", "fatal_error"})
#: Longest ``Retry-After`` we wait out (restricted-tier history is 1/min).
_MAX_RETRY_AFTER_SECONDS = 60
# ponytail: process-local only; a separate worker process still lists itself.
# Upgrade: persist refs on the ingestion job row.
_LISTING_CACHE_TTL_SECONDS = 180
_LISTING_CACHE: dict[tuple, tuple[float, list[SourceRef]]] = {}
_UNRESOLVED = object()


#: `<@U123>` / `<@U123|ada>` and `<#C123>` / `<#C123|general>` in RAW text.
_USER_MENTION = re.compile(r"<@([UW][A-Z0-9]+)(?:\|([^>]+))?>")
_CHANNEL_MENTION = re.compile(r"<#([CG][A-Z0-9]+)(?:\|[^>]*)?>")


def _plain_text(text: str) -> str:
    """Slack mrkdwn -> readable text: unescape ``&amp;`` etc. and unwrap ``<url|label>``.

    Leaves ``*bold*``/``_italic_``: stripping them could eat literal asterisks in code.
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


def _channel_entry(channel_id: str) -> str:
    """The ACL entry naming a CHANNEL rather than a person.

    The write half of `Viewer.acl`; one function, because two spellings fail closed silently.
    """
    return f"channel:{channel_id}"


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
        #: Filled by `_display_name` from the `users.info` reply it already
        #: fetches; never looked up on its own.
        self._user_emails: dict[str, str] = {}
        #: Per-channel access, resolved at most once per adapter (it is per channel, not per thread).
        self._channel_access: dict[str, DocAccess | None] = {}
        self._channels_meta: dict[str, dict] | None = None
        self._bot_user: object = _UNRESOLVED

    def _bot_user_id(self) -> str | None:
        """Our own bot's user id (``auth.test``), lazily, only when a message @mentions someone.

        A failure means "no bot known": the thread is indexed rather than failing the sync.
        """
        if self._bot_user is _UNRESOLVED:
            try:
                self._bot_user = self._get("auth.test", {}).get("user_id") or None
            except SourceError:
                self._bot_user = None
        return self._bot_user  # type: ignore[return-value]

    def _is_bot_traffic(self, message: dict, bot_users: set[str] | None = None) -> bool:
        """A message the BOT posted, or a question addressed TO it.

        Not discussion: indexing them made the bot re-summarise its own old answers.
        """
        if message.get("bot_id"):
            return True
        text = message.get("text") or ""
        if "<@" not in text:
            return False
        if bot_users is None:
            bot = self._bot_user_id()
            bot_users = {bot} if bot else set()
        return any(f"<@{u}>" in text for u in bot_users)

    def _channel_meta(self) -> dict[str, dict]:
        """`conversations.list` once per adapter: it answers for every channel at once."""
        if self._channels_meta is None:
            self._channels_meta = {c["id"]: c for c in list_slack_channels(self._token)}
        return self._channels_meta

    def _access_for(self, channel_id: str) -> DocAccess | None:
        """Who may read the threads in this channel. ``None`` = could not tell.

        Public channel: scope-public. Private channel: its members, plus `channel:<id>`
        so the bot can answer inside it (`Viewer.channels`). Resolved on the LISTING
        (per channel, cheap) because revocation rides it: a membership change moves no
        message `ts` (`_restamp_unchanged_access`).

        ponytail: one `users.info` per member; a bulk `users.list` map if channels grow large.
        """
        if channel_id in self._channel_access:
            return self._channel_access[channel_id]

        access: DocAccess | None = None
        try:
            channel = self._channel_meta().get(channel_id)
            if channel is None:
                # Missing means unknown audience, not public.
                logger.warning(
                    "slack: channel %s is not visible to this token; its sharing "
                    "cannot be read", channel_id,
                )
            elif not channel["is_private"]:
                access = DocAccess.scope_public()
            else:
                members = list_channel_members(self._token, channel_id)
                emails = [m["email"] for m in members if m.get("email")]
                if emails:
                    access = DocAccess.restricted(emails + [_channel_entry(channel_id)])
                else:
                    # No member resolvable: an empty audience would hide it from its members; say unknown.
                    logger.warning(
                        "slack: no member emails resolved for private channel %s",
                        channel_id,
                    )
        except SourceError:
            logger.warning(
                "slack: could not resolve access for channel %s", channel_id, exc_info=True
            )

        self._channel_access[channel_id] = access
        return access

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
                # Honour Slack's own wait; a short cap retried into the same window.
                time.sleep(min(_retry_after_seconds(response, attempt), _MAX_RETRY_AFTER_SECONDS))
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
                time.sleep(min(_retry_after_seconds(response, attempt), _MAX_RETRY_AFTER_SECONDS))
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
            # Same response; the email links a Slack author to a Handbook account.
            email = (profile.get("profile", {}).get("email") or "").strip().lower()
            if email:
                self._user_emails[user_id] = email
        except SourceError:
            name = user_id  # Best-effort: a bad lookup shouldn't fail the whole sync.
        self._user_names[user_id] = name
        return name

    #: ``False`` after a listing that stopped early: the pipeline deletes nothing from it.
    listing_complete: bool = True

    def may_remove(self, external_id: str) -> bool:
        """Would the listing have included this thread? Only then is its absence a deletion.

        History filters on the PARENT ts, so a thread older than the backfill window is
        never listed. A channel no longer picked is removable.
        """
        channel_id, _, ts = external_id.partition(":")
        if channel_id not in self._channel_ids:
            return True
        try:
            return float(ts) >= time.time() - self._settings.backfill_days * 86400
        except ValueError:
            return True

    def list_documents(self) -> list[SourceRef]:
        self.listing_complete = True
        try:
            refs = self._list_documents_from_slack()
        except SourceError:
            cached = self._cached_listing()
            if cached:
                # A cached listing is an old snapshot: never proof that anything newer is gone.
                self.listing_complete = False
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
            channel_access = self._access_for(channel_id)
            cursor = None
            listed_before = len(refs)
            # Log why a channel listed nothing: empty vs every message skipped.
            seen = system = bot = short = 0
            failed = False
            while True:
                if len(refs) >= self._settings.max_documents_per_sync:
                    self.listing_complete = False
                    return refs
                params = {"channel": channel_id, "oldest": oldest, "limit": 200}
                if cursor:
                    params["cursor"] = cursor
                try:
                    data = self._get("conversations.history", params)
                except SourceError:
                    if not refs and cursor is None and channel_id == self._channel_ids[0]:
                        raise  # nothing read at all: let the cache fallback decide
                    # One channel failing must not cost the others or read as deletions.
                    logger.warning("slack: listing of %s stopped early", channel_id, exc_info=True)
                    self.listing_complete = False
                    failed = True
                    break
                for message in data.get("messages", []):
                    seen += 1
                    ts = message.get("ts")
                    if not ts or message.get("subtype"):
                        system += 1
                        continue
                    if self._is_bot_traffic(message):
                        bot += 1
                        continue
                    reply_count = message.get("reply_count", 0)
                    text = (message.get("text") or "").strip()
                    if reply_count == 0 and len(text) < self._settings.min_thread_chars:
                        short += 1
                        continue
                    last_ts = message.get("latest_reply") or ts
                    refs.append(
                        SourceRef(
                            external_id=f"{channel_id}:{ts}",
                            title=self._thread_title(channel_id, text),
                            last_modified=_ts_to_dt(last_ts),
                            source_uri=_thread_uri(channel_id, ts),
                            access=channel_access,
                            # No `last_editor`: this is change detection and makes no user lookups; fetch_document resolves it.
                        )
                    )
                    if len(refs) >= self._settings.max_documents_per_sync:
                        self.listing_complete = False
                        return refs
                cursor = (data.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
            if len(refs) == listed_before and not failed:
                logger.warning(
                    "slack: %s listed no threads: Slack returned %d messages in the last "
                    "%d days (%d system, %d from or to the bot, %d too short)",
                    channel_id, seen, self._settings.backfill_days, system, bot, short,
                )
        return refs

    def channel_labels(self) -> list[str]:
        """The channels this adapter reads, so a report states its coverage (never the whole workspace)."""
        return [self._channel_label(cid) for cid in self._channel_ids]

    def fetch_recent_messages(
        self, since: float, *, max_messages: int = 300
    ) -> tuple[list[dict], list[str]]:
        """Messages posted since ``since`` (unix seconds), as an activity feed (not ingestion).

        Returns ``(messages, truncated_channels)`` so the caller discloses partial coverage.
        ``max_messages`` is split per channel so one busy channel cannot starve the rest;
        history is newest-first, so a cap drops the oldest end.
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
                    # Skip system events and empty posts.
                    if not ts or message.get("subtype") or not text:
                        continue
                    if self._is_bot_traffic(message):
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
                            # Built from channel_id + ts: chat.getPermalink would be one call per message.
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
        # The bot's replies name its user id; no extra call needed.
        bot_users = {m["user"] for m in messages if m.get("bot_id") and m.get("user")}
        if isinstance(self._bot_user, str):
            bot_users.add(self._bot_user)
        human = [m for m in messages if not self._is_bot_traffic(m, bot_users)]
        lines = [f"Channel: #{channel}"]
        if truncated:
            lines.append(_TRUNCATION_MARKER.strip())
        for message in human:
            ts = message.get("ts")
            when = _ts_to_dt(ts)
            stamp = when.strftime("%H:%M") if when else "??:??"
            name = self._display_name(message.get("user"))
            text = (message.get("text") or "").strip()
            lines.append(f"[{stamp}] {name}: {text}")

        # Only bot traffic: an EMPTY document, so ingestion acknowledges it without chunks.
        content = "\n".join(lines) if human else ""
        last_modified = _ts_to_dt(messages[-1].get("ts"))
        title = self._thread_title(channel_id, root_text)

        return SourceDocument(
            external_id=external_id,
            title=title,
            content=content,
            source_uri=_thread_uri(channel_id, thread_ts),
            last_modified=last_modified,
            # The thread's starter: a thread is one document, so a reply cannot be attributed.
            last_editor=self._display_name(messages[0].get("user")) or None,
            # Channel id (survives renames): lets an in-channel question read that channel only.
            tags=[channel_tag(channel_id)],
            meta=self._thread_meta(channel_id, messages),
        )

    def _thread_meta(self, channel_id: str, messages: list[dict]) -> dict | None:
        """Authors, participants and mentions of one thread, with no new calls.

        Authors come from `_display_name`'s cache; a mention is recorded by id only (a
        lookup per mention is too costly). Read from the RAW text, where the ids exist.
        """
        people = []
        for index, message in enumerate(messages):
            user_id = message.get("user")
            if not user_id:
                continue
            people.append(
                person(
                    "slack",
                    role="author" if index == 0 else "participant",
                    external_id=user_id,
                    email=self._user_emails.get(user_id),
                    name=self._user_names.get(user_id),
                )
            )
        links = []
        for message in messages:
            raw = message.get("text") or ""
            for user_id, label in _USER_MENTION.findall(raw):
                people.append(
                    person(
                        "slack",
                        role="mentioned",
                        external_id=user_id,
                        email=self._user_emails.get(user_id),
                        name=self._user_names.get(user_id) or label or None,
                    )
                )
            for mentioned_channel in _CHANNEL_MENTION.findall(raw):
                links.append(link("slack", f"channel:{mentioned_channel}"))
        return build_meta(
            people=people,
            links=links,
            containers=[
                container("slack", "channel", channel_id, self._channel_label(channel_id))
            ],
        )

    def get_last_modified(self, external_id: str) -> datetime | None:
        channel_id, _, thread_ts = external_id.partition(":")
        if not channel_id or not thread_ts:
            raise SourceError(f"Malformed Slack external_id: {external_id!r}")
        messages, _ = self._fetch_thread_messages(channel_id, thread_ts)
        if not messages:
            return None
        return _ts_to_dt(messages[-1].get("ts"))
