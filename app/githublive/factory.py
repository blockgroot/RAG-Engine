"""Single construction point for a ``GitHubReader`` (``build_github_reader``).

``org_id`` is required and both token and scope derive from it, so no reader
can be built that reads across tenants.
"""

from __future__ import annotations

from ..config.settings import GitHubLiveSettings
from ..core.exceptions import ConfigurationError
from .base import GitHubReader
from .rest import RestGitHubReader
from .scope import PROVIDER, load_scope


def build_github_reader(
    org_id: str,
    workspace_id: str | None = None,
    *,
    settings: GitHubLiveSettings | None = None,
) -> GitHubReader:
    """Build a reader for this org's GitHub connection. Raises
    ``ConfigurationError`` when not connected or live reads are off."""
    settings = settings or GitHubLiveSettings.from_env()
    if not settings.enabled:
        raise ConfigurationError(
            "Live GitHub reads are disabled (GITHUB_LIVE_ENABLED=false)."
        )

    # Lazy import keeps the DB/credentials dependency out of import time.
    from ..auth.credentials import get_live_connection_token

    scope = load_scope(org_id, workspace_id)
    token = get_live_connection_token(org_id, PROVIDER, workspace_id)
    return RestGitHubReader(token=token, scope=scope, settings=settings)
