"""Apply the database schema.

Reads ``schema.sql`` (which is idempotent — every statement uses IF NOT EXISTS)
and executes it. Safe to run repeatedly.

Uses a direct connection, NOT the pooled ``get_connection``: this may run against
a brand-new database where the ``vector`` extension does not exist yet, and the
pool's configure hook registers pgvector adapters (which requires the extension).
Migration only runs plain DDL and passes no vector params, so it needs neither
the pool nor the adapters.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from ..config.settings import DatabaseSettings
from ..core.exceptions import ConfigurationError
from .connection import DatabaseError

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def apply_schema(settings: DatabaseSettings | None = None) -> None:
    """Create the extension and tables if they don't already exist."""
    settings = settings or DatabaseSettings.from_env()
    if not settings.url:
        raise ConfigurationError("Missing required database configuration: DATABASE_URL")

    sql = SCHEMA_PATH.read_text()
    try:
        with psycopg.connect(settings.url) as conn:
            conn.execute(sql)
            conn.commit()
    except psycopg.Error as exc:
        raise DatabaseError(f"Failed to apply schema: {exc}", cause=exc) from exc
