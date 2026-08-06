"""Reading and refreshing a connection's stored GitHub scope (Plan Phase 4).

``repos.py`` is pure: it talks to GitHub and validates names, with no database.
This module is the thin DB-facing half — it knows about ``org_id``,
``oauth_connections.source_config``, and how to mint a token. Splitting them
this way keeps the allowlist logic (``resolve_repo``) unit-testable with no
database at all, which matters because that function is a security boundary.

Why the scope is *stored* rather than fetched per question: it changes only when
an admin edits the installation on GitHub, so re-listing every repo on every
question would spend rate limit and latency to re-learn something static. The
trade-off is that a change made on GitHub isn't visible until the scope is
refreshed — which is why ``refresh_installation_scope`` exists and is called
both on connect and (Phase 7) from the Sources UI.
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
    """Return the stored scope for this org's GitHub connection.

    Raises ``ConfigurationError`` when GitHub isn't connected at all, so callers
    can distinguish "not connected" from "connected but nothing authorized".
    """
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
    """Re-read the authorized repo list from GitHub and persist it.

    Preserves the existing ``installation_id``/``account_login`` (they identify
    the connection and are not GitHub's to change here) and replaces only the
    ``repository_selection`` + repo list, so this is safe to call repeatedly.
    """
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
    # Merge rather than overwrite: a future provider-specific key added to this
    # config by another feature shouldn't be dropped by a scope refresh.
    merged = {**config, **scope_to_config(scope)}
    set_connection_config(org_id, PROVIDER, merged, workspace_id=workspace_id)
    return scope
