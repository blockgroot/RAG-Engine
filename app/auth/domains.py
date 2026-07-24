"""Verified company email domains, for auto-join (Phase 13).

Two gates must BOTH pass before an email at ``domain`` auto-joins an org:
1. ``verified_at`` is set — the org proved DNS control over the domain by
   publishing an HMAC-derived TXT record (below), not merely "an admin typed
   it in".
2. ``auto_join_enabled`` is explicitly true — an admin must opt in even after
   verification; verifying a domain never grants access by itself.

This closes the impersonation gap a naive "match the email domain" design has:
without DNS proof, anyone could claim ``acme.com`` and auto-join anyone who
signs up with that domain; without the explicit opt-in, even a verified org
might not want every employee to self-serve in (e.g. they'd rather invite
people by hand). Public email providers (gmail.com etc.) can never be
registered at all — one org claiming a shared provider would auto-join every
other user of that provider.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime

import dns.resolver

from ..config.settings import AuthSettings
from ..core.exceptions import ConfigurationError
from ..db.connection import get_connection

_TXT_HOST_PREFIX = "_ragverify"

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
    verified_at: datetime | None
    auto_join_enabled: bool
    created_at: datetime


@dataclass(frozen=True)
class DomainVerificationInstructions:
    """What an admin needs to publish to prove control of a domain."""

    domain_id: str
    dns_record_name: str
    dns_record_value: str


def _row_to_domain(row) -> OrgDomain:
    return OrgDomain(
        id=row[0],
        org_id=row[1],
        domain=row[2],
        verified_at=row[3],
        auto_join_enabled=row[4],
        created_at=row[5],
    )


_SELECT_COLUMNS = "id::text, org_id::text, domain, verified_at, auto_join_enabled, created_at"


def _expected_txt_value(org_id: str, domain: str, settings: AuthSettings) -> str:
    if not settings.jwt_secret:
        raise ConfigurationError("AUTH_JWT_SECRET must be set to verify domains")
    digest = hmac.new(
        settings.jwt_secret.encode(), f"{org_id}:{domain}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"ragverify={digest}"


def register_domain(
    org_id: str, domain: str, *, settings: AuthSettings | None = None
) -> DomainVerificationInstructions:
    """Register a domain claim and return the DNS TXT record to publish.

    Raises ``ConfigurationError`` if the domain is a known public email
    provider (never claimable) or already registered (by this or another org).
    """
    domain = domain.strip().lower()
    if domain in _BLOCKED_DOMAINS:
        raise ConfigurationError(f"{domain!r} is a public email provider and cannot be claimed")

    settings = settings or AuthSettings.from_env()
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO org_domains (org_id, domain) VALUES (%s, %s) RETURNING id::text",
            (org_id, domain),
        ).fetchone()
    domain_id = row[0]
    return DomainVerificationInstructions(
        domain_id=domain_id,
        dns_record_name=f"{_TXT_HOST_PREFIX}.{domain}",
        dns_record_value=_expected_txt_value(org_id, domain, settings),
    )


def verify_domain(
    org_id: str, domain_id: str, *, settings: AuthSettings | None = None
) -> bool:
    """Check the DNS TXT record and mark the domain verified if it matches.

    Scoped to ``org_id`` — an admin can only verify their own org's claim.
    Returns ``True`` iff verification succeeded (idempotent: re-verifying an
    already-verified domain is a no-op success).
    """
    settings = settings or AuthSettings.from_env()
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM org_domains WHERE id = %s AND org_id = %s",
            (domain_id, org_id),
        ).fetchone()
    if not row:
        raise ConfigurationError("No such domain for this organization")
    record = _row_to_domain(row)
    if record.verified_at is not None:
        return True

    expected = _expected_txt_value(org_id, record.domain, settings)
    host = f"{_TXT_HOST_PREFIX}.{record.domain}"
    try:
        answers = dns.resolver.resolve(host, "TXT")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.DNSException):
        return False

    found = any(
        expected in "".join(part.decode() if isinstance(part, bytes) else part for part in rdata.strings)
        for rdata in answers
    )
    if not found:
        return False

    with get_connection() as conn:
        conn.execute(
            "UPDATE org_domains SET verified_at = now() WHERE id = %s AND org_id = %s",
            (domain_id, org_id),
        )
    return True


def set_auto_join(org_id: str, domain_id: str, enabled: bool) -> bool:
    """Toggle auto-join for a domain. Requires the domain to already be verified.

    Returns ``False`` (no-op) if the domain doesn't exist for this org or isn't
    verified yet — auto-join can never be enabled ahead of verification.
    """
    with get_connection() as conn:
        row = conn.execute(
            "UPDATE org_domains SET auto_join_enabled = %s "
            "WHERE id = %s AND org_id = %s AND verified_at IS NOT NULL "
            "RETURNING id",
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
    """Return the org_id an email auto-joins, or ``None`` if none/not eligible.

    Only ever returns an org for a domain that is BOTH verified AND has
    auto-join explicitly enabled — see the module docstring.
    """
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT org_id::text FROM org_domains "
            "WHERE domain = %s AND verified_at IS NOT NULL AND auto_join_enabled = true",
            (domain,),
        ).fetchone()
    return row[0] if row else None
