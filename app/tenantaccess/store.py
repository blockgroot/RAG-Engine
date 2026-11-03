"""Read-only database logins for tenants (docs/tenant-direct-access-plan.md).

A company's login sees only its own ``chunks``, on the read replica. Creating,
re-keying and dropping the login run in owner functions in schema.sql, because
the app's role cannot create logins. The password exists only in the reply that
hands it over: the database is given its SCRAM verifier, never the password.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets
from dataclasses import dataclass
from datetime import datetime

from ..config.settings import TenantAccessSettings
from ..core.exceptions import ProviderError
from ..db.connection import get_connection

MAX_ALLOWED_IPS = 20
# An allowlist of "half the internet" is not one.
_MIN_PREFIX = {4: 16, 6: 48}
_SCRAM_ITERATIONS = 4096


class LoginExistsError(ProviderError):
    """The organization already has a login; one per tenant."""


@dataclass(frozen=True)
class ReaderLogin:
    role_name: str
    allowed_ips: list[str]
    created_at: datetime


@dataclass(frozen=True)
class ReaderCredentials:
    """Shown once. Nothing keeps the password after this is returned."""

    role_name: str
    password: str
    uri: str


def parse_allowed_ips(raw: list[str]) -> list[str]:
    """Normalize addresses and ranges; raise ValueError naming the bad one."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("At least one IP address or range is required")
    if len(raw) > MAX_ALLOWED_IPS:
        raise ValueError(f"At most {MAX_ALLOWED_IPS} IP addresses or ranges")
    out: list[str] = []
    for item in raw:
        try:
            net = ipaddress.ip_network(str(item).strip(), strict=False)
        except ValueError as exc:
            raise ValueError(f"Not an IP address or range: {item!r}") from exc
        if net.prefixlen < _MIN_PREFIX[net.version]:
            raise ValueError(
                f"Range too wide: {item!r} (narrowest allowed is "
                f"/{_MIN_PREFIX[net.version]})"
            )
        if str(net) not in out:
            out.append(str(net))
    return out


def scram_verifier(password: str) -> str:
    """Postgres's SCRAM-SHA-256 verifier, the form ``CREATE ROLE`` stores."""
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _SCRAM_ITERATIONS)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return (
        f"SCRAM-SHA-256${_SCRAM_ITERATIONS}:{b64(salt)}"
        f"${b64(stored_key)}:{b64(server_key)}"
    )


def connection_uri(role_name: str, password: str, settings: TenantAccessSettings) -> str:
    return (
        f"postgresql://{role_name}:{password}@{settings.replica_host}:"
        f"{settings.replica_port}/{settings.database}?sslmode=verify-full"
    )


def get_login(org_id: str) -> ReaderLogin | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT role_name, allowed_ips::text[], created_at "
            "FROM tenant_reader_logins WHERE org_id = %s",
            (org_id,),
        ).fetchone()
    return ReaderLogin(row[0], list(row[1]), row[2]) if row else None


def create_login(
    org_id: str,
    allowed_ips: list[str],
    created_by: str | None,
    settings: TenantAccessSettings | None = None,
) -> ReaderCredentials:
    settings = settings or TenantAccessSettings.from_env()
    ips = parse_allowed_ips(allowed_ips)
    password = secrets.token_urlsafe(32)
    with get_connection() as conn:
        if conn.execute(
            "SELECT 1 FROM tenant_reader_logins WHERE org_id = %s", (org_id,)
        ).fetchone():
            raise LoginExistsError("This organization already has a database login")
        role_name = conn.execute(
            "SELECT tenant_reader_create(%s, %s, %s::cidr[], %s, %s, %s)",
            (
                org_id,
                scram_verifier(password),
                ips,
                settings.connection_limit,
                settings.statement_timeout_seconds * 1000,
                created_by,
            ),
        ).fetchone()[0]
    return ReaderCredentials(role_name, password, connection_uri(role_name, password, settings))


def reset_password(
    org_id: str, settings: TenantAccessSettings | None = None
) -> ReaderCredentials | None:
    """New password; the old one stops working at once. None: no login."""
    settings = settings or TenantAccessSettings.from_env()
    login = get_login(org_id)
    if login is None:
        return None
    password = secrets.token_urlsafe(32)
    with get_connection() as conn:
        changed = conn.execute(
            "SELECT tenant_reader_set_password(%s, %s)", (org_id, scram_verifier(password))
        ).fetchone()[0]
    if not changed:
        return None
    return ReaderCredentials(
        login.role_name, password, connection_uri(login.role_name, password, settings)
    )


def set_allowed_ips(org_id: str, allowed_ips: list[str]) -> bool:
    ips = parse_allowed_ips(allowed_ips)
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE tenant_reader_logins SET allowed_ips = %s::cidr[] WHERE org_id = %s",
            (ips, org_id),
        )
        return cur.rowcount == 1


def drop_login(org_id: str) -> bool:
    """Remove the login. False when there was none (idempotent)."""
    with get_connection() as conn:
        return bool(
            conn.execute("SELECT tenant_reader_drop(%s)", (org_id,)).fetchone()[0]
        )


def list_logins() -> list[tuple[str, list[str]]]:
    """Every login and its addresses, for the replica's gatekeeper (hba.py)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT role_name, allowed_ips::text[] FROM tenant_reader_logins "
            "ORDER BY role_name"
        ).fetchall()
    return [(r[0], list(r[1])) for r in rows]
