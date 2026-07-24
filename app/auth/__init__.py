"""Admin-driven OAuth "Connect X" flows (Phase 11).

Public API:
    from app.auth import build_oauth_provider
    provider = build_oauth_provider("notion")
    url = provider.authorize_url(state)
    tokens = provider.exchange_code(code)
"""

from .base import OAuthProvider, OAuthTokens
from .notion_oauth import NotionOAuthProvider
from .factory import build_oauth_provider
from .credentials import (
    OAuthConnectionInfo,
    save_connection,
    get_connection_token,
    list_connections,
)

__all__ = [
    "OAuthProvider",
    "OAuthTokens",
    "NotionOAuthProvider",
    "build_oauth_provider",
    "OAuthConnectionInfo",
    "save_connection",
    "get_connection_token",
    "list_connections",
]
