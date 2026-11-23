"""Apply ``schema.sql`` (idempotent; safe to rerun) via ``apply_schema()``.

A direct connection, not the pool: the ``vector`` extension may not exist
yet, and the pool's hook needs it.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from ..config.settings import DatabaseSettings
from ..core.exceptions import ConfigurationError
from .connection import DatabaseError

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

#: Advisory lock key for ``apply_schema`` (any constant no other code uses).
_SCHEMA_LOCK = 0x48424B53  # "HBKS"


def apply_schema(settings: DatabaseSettings | None = None) -> None:
    """Create the extension and tables if they don't already exist."""
    settings = settings or DatabaseSettings.from_env()
    if not settings.url:
        raise ConfigurationError("Missing required database configuration: DATABASE_URL")

    sql = SCHEMA_PATH.read_text()
    try:
        with psycopg.connect(settings.url) as conn:
            # One apply at a time: two instances booting together raced the
            # CREATE ... IF NOT EXISTS statements and the schema_marks fixes.
            # Transaction-scoped, so it is released by the commit below.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK,))
            conn.execute(sql)
            conn.commit()
    except psycopg.Error as exc:
        raise DatabaseError(f"Failed to apply schema: {exc}", cause=exc) from exc
