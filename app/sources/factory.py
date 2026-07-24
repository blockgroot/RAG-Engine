"""Single construction point for a content-source adapter.

Callers do ``build_source_adapter()`` and get back something satisfying the
``SourceAdapter`` interface. Adding Google Drive / GitHub / Slack later means
adding a branch here (keyed on ``SOURCE_TYPE``) — callers don't change.
"""

from __future__ import annotations

from ..config.settings import DEFAULT_SOURCE_TYPE, NotionSettings
from ..core.exceptions import ConfigurationError
from .base import SourceAdapter
from .notion import NotionAdapter


def build_source_adapter(
    source_type: str = DEFAULT_SOURCE_TYPE,
    *,
    token_name: str | None = None,
    token: str | None = None,
) -> SourceAdapter:
    """Build the configured source adapter (defaults to Notion).

    Two independent, non-fallback-linked ways to select a credential (never
    mix them for one call — each is its own explicit source of truth):

    - ``token``  the exact secret to use, already resolved by the caller. This
      is how Phase 12's job worker points ingestion at an org's *OAuth-connected*
      credential (``app.auth.get_connection_token``) without touching env vars.
    - ``token_name``  selects a ``NOTION_TOKEN_<NAME>`` env var (Phase 9), resolved
      via ``NotionSettings.resolve_token`` with **no** fallback to another org's
      token. Used only when ``token`` is not supplied.

    When neither is given, the default ``NOTION_TOKEN`` is used (Phase 4).
    """
    source_type = (source_type or DEFAULT_SOURCE_TYPE).lower()

    if source_type == "notion":
        settings = NotionSettings.from_env()
        resolved = token or settings.resolve_token(token_name)
        return NotionAdapter(settings=settings, token=resolved)

    raise ConfigurationError(
        f"Unknown source type: {source_type!r} (expected 'notion')"
    )
