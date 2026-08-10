"""Identity, OAuth "Connect X" flows, and sessions (Phases 10-13).

Public API:
    from app.auth import build_oauth_provider
    provider = build_oauth_provider("notion")
    url = provider.authorize_url(state)
    tokens = provider.exchange_code(code)
"""

from .base import OAuthProvider, OAuthTokens
from .notion_oauth import NotionOAuthProvider
from .google_oauth import GoogleOAuthProvider
from .github_oauth import GitHubAppProvider
from .github_app import InstallationToken, mint_installation_token
from .factory import build_oauth_provider
from .credentials import (
    OAuthConnectionInfo,
    save_connection,
    get_connection_token,
    get_live_connection_token,
    list_connections,
    set_connection_config,
    get_connection_config,
    delete_connection,
    clear_installation_token_cache,
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
    revoke_user_sessions,
    remove_member,
)
from .magic_link import create_magic_link_token, consume_magic_link_token
from .signup_requests import (
    SignupRequest,
    create_signup_request,
    get_pending_request_for_email,
    consume_approve_token,
    consume_reject_token,
    get_request_by_approve_token,
    get_request_by_reject_token,
)
from .oauth_state import create_state, consume_state
from .session import SessionClaims, create_session_token, decode_session_token
from .email import (
    send_magic_link_email,
    send_magic_link_email_safe,
    send_signup_approved_email,
    send_signup_approved_email_safe,
    send_signup_rejected_email,
    send_signup_rejected_email_safe,
    send_signup_request_notification_email,
    send_signup_request_notification_email_safe,
)

__all__ = [
    "OAuthProvider",
    "OAuthTokens",
    "NotionOAuthProvider",
    "GoogleOAuthProvider",
    "GitHubAppProvider",
    "InstallationToken",
    "mint_installation_token",
    "build_oauth_provider",
    "OAuthConnectionInfo",
    "save_connection",
    "get_connection_token",
    "get_live_connection_token",
    "list_connections",
    "set_connection_config",
    "get_connection_config",
    "clear_installation_token_cache",
    "delete_connection",
    "User",
    "ROLE_ADMIN",
    "ROLE_MEMBER",
    "get_user",
    "get_user_by_email",
    "create_user",
    "create_admin",
    "invite_member",
    "list_members",
    "revoke_user_sessions",
    "remove_member",
    "create_magic_link_token",
    "consume_magic_link_token",
    "SignupRequest",
    "create_signup_request",
    "get_pending_request_for_email",
    "consume_approve_token",
    "consume_reject_token",
    "get_request_by_approve_token",
    "get_request_by_reject_token",
    "create_state",
    "consume_state",
    "SessionClaims",
    "create_session_token",
    "decode_session_token",
    "send_magic_link_email",
    "send_magic_link_email_safe",
    "send_signup_approved_email",
    "send_signup_approved_email_safe",
    "send_signup_rejected_email",
    "send_signup_rejected_email_safe",
    "send_signup_request_notification_email",
    "send_signup_request_notification_email_safe",
]
