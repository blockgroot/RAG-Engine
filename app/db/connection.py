"""Postgres connection helpers.

One place that knows how to open a connection from ``DatabaseSettings`` and
register the pgvector type adapters. Everything else (migrations, the pgvector
store) goes through here rather than calling ``psycopg.connect`` directly.

A simple per-call connection is used for now; a pool can be introduced here later
without touching callers.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector

from ..config.settings import DatabaseSettings
from ..core.exceptions import ConfigurationError, ProviderError


class DatabaseError(ProviderError):
    """Raised when a database connection or query fails."""


@contextmanager
def get_connection(settings: DatabaseSettings | None = None) -> Iterator["psycopg.Connection"]:
    """Yield an open psycopg connection with pgvector registered.

    Commits on clean exit, rolls back on exception, and always closes.
    """
    settings = settings or DatabaseSettings.from_env()
    if not settings.url:
        raise ConfigurationError("Missing required database configuration: DATABASE_URL")

    try:
        conn = psycopg.connect(settings.url)
    except psycopg.Error as exc:
        raise DatabaseError(
            f"Could not connect to the database: {exc}", cause=exc
        ) from exc

    try:
        # Registering the pgvector adapters needs the extension to already exist.
        # During the very first migration it won't yet — that's fine, migrations
        # don't pass vector params. Store operations run on later connections
        # (after the schema is applied) where registration succeeds.
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            conn.rollback()  # clear the aborted-transaction state

        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
