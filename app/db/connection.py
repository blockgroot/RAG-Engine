"""Postgres connection helpers, backed by a psycopg connection pool.

One place that knows how to talk to Postgres. Everything else (the pgvector
store, test cleanup) goes through ``get_connection()`` rather than calling
``psycopg.connect`` directly, so pooling is transparent to callers.

Lifecycle
---------
The pool is created lazily on first use and lives at module scope. Every process
that uses the DB should close it at its own boundary via ``close_pool()`` (see
``scripts/*`` mains and the test teardown) so connections are released cleanly —
this matters more once a long-running API server exists, but we wire it now so it
isn't a loose end later.

Note: schema migration (``migrate.apply_schema``) deliberately does NOT use this
pool — it opens a direct connection, because it may run against a brand-new
database where the ``vector`` extension (and thus the pgvector adapters
registered here) does not exist yet.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from ..config.settings import DatabaseSettings
from ..core.exceptions import ConfigurationError, ProviderError


class DatabaseError(ProviderError):
    """Raised when a database connection or query fails."""


# Module-level singleton pool, keyed by the URL it was opened for.
_pool: ConnectionPool | None = None
_pool_url: str | None = None


def _configure(conn: "psycopg.Connection") -> None:
    """Run once per pooled connection: register pgvector type adapters.

    Registering needs the ``vector`` extension to exist. Swallowing a miss used
    to leave connections in the pool that cannot bind numpy/``Vector`` values —
    after ``docker compose down -v`` + re-init that surfaces as
    ``cannot adapt type 'ndarray'`` on ingest. Fail closed here; callers that
    hit a brand-new DB should run ``scripts/init_db.py`` first.
    """
    try:
        register_vector(conn)
    except psycopg.ProgrammingError as exc:
        conn.rollback()
        raise DatabaseError(
            "pgvector adapters could not be registered — is the schema applied "
            "(scripts/init_db.py)?",
            cause=exc,
        ) from exc


def get_pool(settings: DatabaseSettings | None = None) -> ConnectionPool:
    """Return the shared connection pool, creating it on first call."""
    global _pool, _pool_url
    settings = settings or DatabaseSettings.from_env()
    if not settings.url:
        raise ConfigurationError("Missing required database configuration: DATABASE_URL")

    # If asked for a different URL than the open pool (e.g. tests), reset.
    if _pool is not None and _pool_url != settings.url:
        close_pool()

    if _pool is None:
        try:
            _pool = ConnectionPool(
                conninfo=settings.url,
                min_size=settings.pool_min_size,
                max_size=settings.pool_max_size,
                configure=_configure,
                open=True,
            )
        except psycopg.Error as exc:
            raise DatabaseError(
                f"Could not open the connection pool: {exc}", cause=exc
            ) from exc
        _pool_url = settings.url

    return _pool


def close_pool() -> None:
    """Close the shared pool if open. Safe to call multiple times."""
    global _pool, _pool_url
    if _pool is not None:
        _pool.close()
        _pool = None
        _pool_url = None


@contextmanager
def get_connection(settings: DatabaseSettings | None = None) -> Iterator["psycopg.Connection"]:
    """Yield a pooled connection with pgvector registered.

    The pool's context manager commits on clean exit, rolls back on exception,
    and returns the connection to the pool (it is not closed).
    """
    pool = get_pool(settings)
    try:
        with pool.connection() as conn:
            # Re-register on checkout: a connection created before the extension
            # existed, or recycled after a DB wipe, otherwise keeps dumping
            # embeddings as bare ndarrays and Postgres rejects them.
            try:
                register_vector(conn)
            except psycopg.ProgrammingError as exc:
                conn.rollback()
                raise DatabaseError(
                    "pgvector adapters could not be registered — is the schema "
                    "applied (scripts/init_db.py)?",
                    cause=exc,
                ) from exc
            yield conn
    except psycopg.Error as exc:
        raise DatabaseError(f"Database operation failed: {exc}", cause=exc) from exc
