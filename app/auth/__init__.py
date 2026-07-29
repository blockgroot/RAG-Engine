"""Identity, OAuth "Connect X" flows, and sessions (Phases 10-13).

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
from .users import (
    User,
    ROLE_ADMIN,
    ROLE_MEMBER,
    get_user,
    get_user_by_email,
    create_user,
    create_admin,
    invite_member,
    list_members,
)
from .magic_link import create_magic_link_token, consume_magic_link_token
from .oauth_state import create_state, consume_state
from .session import SessionClaims, create_session_token, decode_session_token
from .email import send_magic_link_email, send_magic_link_email_safe

__all__ = [
    "OAuthProvider",
    "OAuthTokens",
    "NotionOAuthProvider",
    "build_oauth_provider",
    "OAuthConnectionInfo",
    "save_connection",
    "get_connection_token",
    "list_connections",
    "User",
    "ROLE_ADMIN",
    "ROLE_MEMBER",
    "get_user",
    "get_user_by_email",
    "create_user",
    "create_admin",
    "invite_member",
    "list_members",
    "create_magic_link_token",
    "consume_magic_link_token",
    "create_state",
    "consume_state",
    "SessionClaims",
    "create_session_token",
    "decode_session_token",
    "send_magic_link_email",
    "send_magic_link_email_safe",
]
