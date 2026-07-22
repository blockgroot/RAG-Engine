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


def build_source_adapter(source_type: str = DEFAULT_SOURCE_TYPE) -> SourceAdapter:
    """Build the configured source adapter (defaults to Notion)."""
    source_type = (source_type or DEFAULT_SOURCE_TYPE).lower()

    if source_type == "notion":
        return NotionAdapter(settings=NotionSettings.from_env())

    raise ConfigurationError(
        f"Unknown source type: {source_type!r} (expected 'notion')"
    )
