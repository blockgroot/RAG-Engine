"""Single construction point for a ``GitHubReader``.

Callers do ``build_github_reader(org_id)`` and get something satisfying the
``GitHubReader`` interface, with the tenant's token and authorized scope already
resolved. Same shape as ``app/sources/factory.py`` and ``app/llm/factory.py``.

The important property: ``org_id`` is **required**, and both the token and the
scope are derived from it here. There is no way to construct a reader without
naming a tenant, so a caller cannot accidentally build one that reads across
tenants — the same reason every vector-store read requires an ``org_id``.
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
    """Build a reader for this org's GitHub connection.

    Raises ``ConfigurationError`` when GitHub isn't connected, or when live reads
    are disabled by configuration — both are "no GitHub answers available", which
    the agent turns into its fixed fallback rather than an error to the user.
    """
    settings = settings or GitHubLiveSettings.from_env()
    if not settings.enabled:
        raise ConfigurationError(
            "Live GitHub reads are disabled (GITHUB_LIVE_ENABLED=false)."
        )

    # Lazy import: keeps the DB/credentials dependency out of module import time,
    # matching how credentials.py lazily imports its provider factory.
    from ..auth.credentials import get_live_connection_token

    scope = load_scope(org_id, workspace_id)
    token = get_live_connection_token(org_id, PROVIDER, workspace_id)
    return RestGitHubReader(token=token, scope=scope, settings=settings)
