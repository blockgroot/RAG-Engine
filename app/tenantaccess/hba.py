"""The replica's pg_hba.conf lines for tenant logins.

The allowlist is recorded in ``tenant_reader_logins``; it is enforced where
connections are accepted. On a self-managed replica that is pg_hba.conf: put
these lines BEFORE any broader rule and reload Postgres. On a managed database
(RDS security group, Supabase Network Restrictions) apply the same addresses
there. ``python -m app.tenantaccess`` prints them.
"""

from __future__ import annotations


def hba_lines(logins: list[tuple[str, list[str]]], database: str) -> str:
    """One SSL-only line per login and address, then refuse every other reader."""
    lines = [
        f"hostssl  {database}  {role}  {cidr}  scram-sha-256"
        for role, cidrs in logins
        for cidr in cidrs
    ]
    lines.append("host     all  +tenant_readers  all  reject")
    return "\n".join(lines) + "\n"

