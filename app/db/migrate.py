"""Apply the database schema.

Reads ``schema.sql`` (which is idempotent — every statement uses IF NOT EXISTS)
and executes it. Safe to run repeatedly.
"""

from __future__ import annotations

from pathlib import Path

from ..config.settings import DatabaseSettings
from .connection import get_connection

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def apply_schema(settings: DatabaseSettings | None = None) -> None:
    """Create the extension and tables if they don't already exist."""
    sql = SCHEMA_PATH.read_text()
    with get_connection(settings) as conn:
        conn.execute(sql)
