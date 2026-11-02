"""Tenant direct access: one read-only database login per organization.

A single-implementation package like ``app/workspaces/``: Postgres is the only
backend, so there is no ``base.py``/factory.
"""

from __future__ import annotations

from .hba import hba_lines
from .store import (
    LoginExistsError,
    ReaderCredentials,
    ReaderLogin,
    create_login,
    drop_login,
    get_login,
    list_logins,
    parse_allowed_ips,
    reset_password,
    set_allowed_ips,
)

__all__ = [
    "LoginExistsError",
    "ReaderCredentials",
    "ReaderLogin",
    "create_login",
    "drop_login",
    "get_login",
    "hba_lines",
    "list_logins",
    "parse_allowed_ips",
    "reset_password",
    "set_allowed_ips",
]
