"""Single construction point for a content-source adapter.

Callers do ``build_source_adapter()`` and get back something satisfying the
``SourceAdapter`` interface. Adding Google Drive / GitHub / Slack later means
adding a branch here (keyed on ``SOURCE_TYPE``) — callers don't change.
"""

from __future__ import annotations

from ..config.settings import DEFAULT_SOURCE_TYPE, LinearSettings, NotionSettings
from ..core.exceptions import ConfigurationError
from .base import SourceAdapter
from .google_drive import GoogleDriveAdapter
from .linear import LinearAdapter
from .notion import NotionAdapter
from .slack import SlackAdapter


def build_source_adapter(
    source_type: str = DEFAULT_SOURCE_TYPE,
    *,
    token_name: str | None = None,
    token: str | None = None,
    config: dict | None = None,
) -> SourceAdapter:
    """Build the configured source adapter (defaults to Notion).

    Two independent, non-fallback-linked ways to select a credential (never
    mix them for one call — each is its own explicit source of truth):

    - ``token``  the exact secret to use, already resolved by the caller. This
      is how Phase 12's job worker points ingestion at an org's *OAuth-connected*
      credential (``app.auth.get_live_connection_token``) without touching env vars.
    - ``token_name``  selects a ``NOTION_TOKEN_<NAME>`` env var (Phase 9), resolved
      via ``NotionSettings.resolve_token`` with **no** fallback to another org's
      token. Used only when ``token`` is not supplied.

    When neither is given, the default ``NOTION_TOKEN`` is used (Phase 4).

    ``config`` is the optional provider-specific ingestion scope from
    ``oauth_connections.source_config`` (Google Integration Phase 6). Notion
    ignores it (visibility is already scoped by what was shared with the
    integration). Google requires ``config["folder_id"]`` — a Drive OAuth
    grant has no equivalent implicit scoping — and ``token`` must already be
    an OAuth-resolved access token (never a ``token_name`` env lookup; that
    plumbing is Notion-Phase-9-specific).
    """
    source_type = (source_type or DEFAULT_SOURCE_TYPE).lower()

    if source_type == "notion":
        settings = NotionSettings.from_env()
        resolved = token or settings.resolve_token(token_name)
        return NotionAdapter(settings=settings, token=resolved)

    if source_type == "google":
        if not token:
            raise ConfigurationError(
                "build_source_adapter('google', ...) requires an OAuth access "
                "token (pass token= from get_live_connection_token)."
            )
        folder_id = (config or {}).get("folder_id")
        if not folder_id:
            raise ConfigurationError(
                "Google Drive connection has no folder configured. Paste a "
                "Drive folder URL on the Sources page before syncing."
            )
        return GoogleDriveAdapter(token=token, folder_id=folder_id)

    if source_type == "slack":
        if not token:
            raise ConfigurationError(
                "build_source_adapter('slack', ...) requires an OAuth bot "
                "token (pass token= from get_live_connection_token)."
            )
        channel_ids = (config or {}).get("channel_ids")
        if not channel_ids:
            raise ConfigurationError(
                "Slack connection has no channels configured. Pick at least "
                "one channel on the Sources page before syncing."
            )
        channel_names = (config or {}).get("channel_names") or {}
        return SlackAdapter(token=token, channel_ids=channel_ids, channel_names=channel_names)

    if source_type == "linear":
        settings = LinearSettings.from_env()
        if token:
            # A directly-passed token means the OAuth-connected path (the job
            # worker's get_live_connection_token) — Linear sends the header
            # differently for an OAuth token vs. a personal API key, so this
            # is what tells LinearAdapter which one it holds.
            return LinearAdapter(settings=settings, token=token, oauth=True)
        resolved = settings.resolve_token(token_name)
        return LinearAdapter(settings=settings, token=resolved, oauth=False)

    raise ConfigurationError(
        f"Unknown source type: {source_type!r} (expected 'notion', 'google', 'slack', or 'linear')"
    )
