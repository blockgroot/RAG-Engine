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
from .users import User, ROLE_ADMIN, ROLE_MEMBER, get_user, get_user_by_email, create_user, get_or_create_member, create_admin
from .domains import (
    OrgDomain,
    DomainVerificationInstructions,
    register_domain,
    verify_domain,
    set_auto_join,
    list_domains,
    resolve_org_for_email,
)
from .magic_link import create_magic_link_token, consume_magic_link_token
from .oauth_state import create_state, consume_state
from .session import SessionClaims, create_session_token, decode_session_token
from .email import send_magic_link_email

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
    "get_or_create_member",
    "create_admin",
    "OrgDomain",
    "DomainVerificationInstructions",
    "register_domain",
    "verify_domain",
    "set_auto_join",
    "list_domains",
    "resolve_org_for_email",
    "create_magic_link_token",
    "consume_magic_link_token",
    "create_state",
    "consume_state",
    "SessionClaims",
    "create_session_token",
    "decode_session_token",
    "send_magic_link_email",
]
