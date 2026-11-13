"""Single construction point for a content-source adapter (``build_source_adapter``)."""

from __future__ import annotations

from ..config.settings import DEFAULT_SOURCE_TYPE, LinearSettings, NotionSettings
from ..core.exceptions import ConfigurationError
from .base import SourceAdapter
from .google_drive import GoogleDriveAdapter
from .linear import LinearAdapter
from .notion import NotionAdapter
from .slack import SlackAdapter


#: Providers with a `SourceAdapter` (they write `documents`/`chunks`). The one
#: answer to "does disconnecting this leave a corpus behind?": hand-kept copies missed Linear.
INDEXED_PROVIDERS: tuple[str, ...] = ("notion", "google", "slack", "linear")

#: Providers whose adapter reports a per-document ACL (`base.DocAccess`); only
#: these fail closed when `access is None` (a skip elsewhere would empty Notion).
#: Notion is absent: its API has no per-page permissions. Say so in the UI; a
#: half-enforced guarantee that reads as whole is worse than none.
ACL_CAPABLE: frozenset[str] = frozenset({"google", "slack", "linear"})


def build_source_adapter(
    source_type: str = DEFAULT_SOURCE_TYPE,
    *,
    token_name: str | None = None,
    token: str | None = None,
    config: dict | None = None,
) -> SourceAdapter:
    """Build the configured source adapter (defaults to Notion).

    Two credential paths, never mixed: ``token`` (already resolved, e.g. an OAuth
    connection) or ``token_name`` (a ``NOTION_TOKEN_<NAME>`` env var, no fallback to
    another org's). Neither: the default ``NOTION_TOKEN``.

    ``config`` is the connection's ``source_config`` scope; Notion ignores it,
    Google requires ``config["folder_id"]`` and an OAuth ``token``.
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
            # A passed token is OAuth: Linear sends it as Bearer, a personal key raw.
            team_ids = (config or {}).get("team_ids")
            if not team_ids:
                raise ConfigurationError(
                    "Linear connection has no teams configured. Pick at least "
                    "one team on the Sources page before syncing."
                )
            return LinearAdapter(settings=settings, token=token, oauth=True, team_ids=team_ids)
        resolved = settings.resolve_token(token_name)
        return LinearAdapter(settings=settings, token=resolved, oauth=False)

    raise ConfigurationError(
        f"Unknown source type: {source_type!r} (expected 'notion', 'google', 'slack', or 'linear')"
    )
