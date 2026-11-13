"""Reading and refreshing a connection's stored GitHub scope (the DB half).

Kept apart from ``repos.py`` so the ``resolve_repo`` security boundary is
testable without a database. Stored, not fetched per question; refreshed by
``refresh_installation_scope``.
"""

from __future__ import annotations

from ..auth.credentials import (
    get_connection_config,
    get_live_connection_token,
    set_connection_config,
)
from ..core.exceptions import ConfigurationError
from .repos import InstallationScope, fetch_installation_repos, scope_from_config, scope_to_config

PROVIDER = "github"


def load_scope(org_id: str, workspace_id: str | None = None) -> InstallationScope:
    """The stored scope for this org's GitHub connection. Raises
    ``ConfigurationError`` when not connected (unlike "nothing authorized")."""
    config = get_connection_config(org_id, PROVIDER, workspace_id)
    if not config:
        raise ConfigurationError(
            "GitHub is not connected for this organization. An admin must connect "
            "it before repository questions can be answered."
        )
    return scope_from_config(config)


def refresh_installation_scope(
    org_id: str, workspace_id: str | None = None
) -> InstallationScope:
    """Re-read the authorized repo list from GitHub and persist it, keeping
    ``installation_id``/``account_login``. Idempotent."""
    config = get_connection_config(org_id, PROVIDER, workspace_id) or {}
    installation_id = str(config.get("installation_id") or "")
    if not installation_id:
        raise ConfigurationError(
            "This GitHub connection has no installation id recorded; reconnect GitHub."
        )

    token = get_live_connection_token(org_id, PROVIDER, workspace_id)
    selection, repos = fetch_installation_repos(token)

    scope = InstallationScope(
        installation_id=installation_id,
        account_login=str(config.get("account_login") or ""),
        repository_selection=selection,
        repos=tuple(repos),
    )
    # Merge, so other keys in this config survive a refresh.
    merged = {**config, **scope_to_config(scope)}
    url = _settings_url(installation_id)
    if url:
        merged["manage_url"] = url
    set_connection_config(org_id, PROVIDER, merged, workspace_id=workspace_id)
    return scope


def with_manage_url(org_id: str, workspace_id: str | None, config: dict | None) -> dict | None:
    """The config with the installation's GitHub settings page (``manage_url``),
    read once and kept; older connections get it when their card is first shown."""
    if not config or config.get("manage_url") or not config.get("installation_id"):
        return config
    url = _settings_url(str(config["installation_id"]))
    if not url:
        return config
    merged = {**config, "manage_url": url}
    set_connection_config(org_id, PROVIDER, merged, workspace_id=workspace_id)
    return merged


def _settings_url(installation_id: str) -> str | None:
    from ..auth.github_app import installation_settings_url

    try:
        return installation_settings_url(installation_id)
    except Exception:  # noqa: BLE001 - only a link on a card; never fail a refresh over it
        return None
