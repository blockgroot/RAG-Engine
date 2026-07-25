"""Company email domains allowed to auto-join an org (simplified auth).

An admin types in their company's domain (e.g. "acme.com") and toggles
``auto_join_enabled``; any email at that domain can then request a magic
link and land in that org. There is deliberately no DNS-ownership proof step
— that was real friction for a small team with no one who can publish a DNS
TXT record — so this trusts the admin's word on their own domain, the same
level of trust the admin already has to invite/manage everything else in
their org. Public email providers (gmail.com etc.) can never be registered:
one org claiming a shared provider would auto-join every other user of it,
which is the one impersonation risk cheap enough to still guard against.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core.exceptions import ConfigurationError
from ..db.connection import get_connection

# One org owning a shared public provider would auto-join every user of it.
_BLOCKED_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "me.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
        "gmx.com",
        "yandex.com",
        "mail.com",
    }
)


@dataclass(frozen=True)
class OrgDomain:
    id: str
    org_id: str
    domain: str
    auto_join_enabled: bool
    created_at: datetime


_SELECT_COLUMNS = "id::text, org_id::text, domain, auto_join_enabled, created_at"


def _row_to_domain(row) -> OrgDomain:
    return OrgDomain(id=row[0], org_id=row[1], domain=row[2], auto_join_enabled=row[3], created_at=row[4])


def register_domain(org_id: str, domain: str) -> OrgDomain:
    """Register a domain claim, live immediately (auto-join on by default).

    Raises ``ConfigurationError`` if the domain is a known public email
    provider (never claimable) or already registered (by this or another org).
    """
    domain = domain.strip().lower()
    if domain in _BLOCKED_DOMAINS:
        raise ConfigurationError(f"{domain!r} is a public email provider and cannot be claimed")

    with get_connection() as conn:
        row = conn.execute(
            f"INSERT INTO org_domains (org_id, domain) VALUES (%s, %s) "
            f"RETURNING {_SELECT_COLUMNS}",
            (org_id, domain),
        ).fetchone()
    return _row_to_domain(row)


def set_auto_join(org_id: str, domain_id: str, enabled: bool) -> bool:
    """Toggle auto-join for a domain. Returns ``False`` if it doesn't belong to this org."""
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE org_domains SET auto_join_enabled = %s WHERE id = %s AND org_id = %s RETURNING id",
            (enabled, domain_id, org_id),
        ).fetchone()
    return row is not None


def list_domains(org_id: str) -> list[OrgDomain]:
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM org_domains WHERE org_id = %s ORDER BY created_at",
            (org_id,),
        ).fetchall()
    return [_row_to_domain(row) for row in rows]


def resolve_org_for_email(email: str) -> str | None:
    """Return the org_id an email auto-joins, or ``None`` if none/not eligible."""
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id::text FROM org_domains WHERE domain = %s AND auto_join_enabled = true",
            (domain,),
        ).fetchone()
    return row[0] if row else None
