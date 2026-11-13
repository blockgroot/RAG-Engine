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
