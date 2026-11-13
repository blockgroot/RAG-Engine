"""Postgres connection helpers over a psycopg pool.

Everything goes through ``get_connection()``. The pool is created lazily;
every process calls ``close_pool()`` at its boundary. ``migrate.apply_schema``
opens its own connection: the ``vector`` extension may not exist yet.
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


# Marker on a physical connection once its pgvector adapters are registered.
_REGISTERED = "_handbook_pgvector_registered"


def _register_vector_once(conn: "psycopg.Connection") -> None:
    """Register pgvector adapters on ``conn``, at most once per connection.

    ``register_vector`` costs a server round trip per type; run on every
    checkout it added ~3s per query on a cross-region deploy. An unmarked
    connection (made before the extension existed) is still registered on
    checkout; after recreating the extension, call ``close_pool()``. Fails
    closed: an unregistered pooled connection is worse than an error.
    """
    if getattr(conn, _REGISTERED, False):
        return
    try:
        register_vector(conn)
    except psycopg.ProgrammingError as exc:
        conn.rollback()
        raise DatabaseError(
            "pgvector adapters could not be registered — is the schema applied "
            "(scripts/init_db.py)?",
            cause=exc,
        ) from exc
    setattr(conn, _REGISTERED, True)


def _configure(conn: "psycopg.Connection") -> None:
    """Pool hook, run once per new physical connection.

    Switches to ``handbook_app`` so the row-level policy on ``chunks`` applies
    (the URL's owner bypasses it); a connection that cannot switch never joins
    the pool. Sets ``hnsw.iterative_scan = strict_order``: tenants share one
    index, and a plain scan returned 3.9 of 10 rows at 1.1M vectors. Needs
    pgvector 0.8+ (inert before).
    """
    try:
        conn.execute("SET ROLE handbook_app")
        conn.commit()
    except psycopg.Error as exc:
        conn.rollback()
        raise DatabaseError(
            "Could not act as handbook_app — is the schema applied "
            "(scripts/init_db.py)?",
            cause=exc,
        ) from exc
    _register_vector_once(conn)
    conn.execute("SET hnsw.iterative_scan = strict_order")
    conn.commit()


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
def get_connection(
    settings: DatabaseSettings | None = None,
    *,
    org_id: str | None = None,
    all_orgs: bool = False,
) -> Iterator["psycopg.Connection"]:
    """Yield a pooled connection with pgvector registered (commit on exit, rollback on error).

    ``chunks`` shows only ``org_id``'s rows, none without it (row-level policy);
    ``all_orgs`` is for cross-tenant background jobs. Both end with the
    transaction, so a ``commit()`` in the block ends them too.
    """
    pool = get_pool(settings)
    try:
        with pool.connection() as conn:
            # Safety net for a connection that skipped the configure hook; a no-op once registered.
            _register_vector_once(conn)
            if org_id is not None:
                conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org_id),))
            if all_orgs:
                conn.execute("SELECT set_config('app.all_orgs', 'on', true)")
            yield conn
    except psycopg.Error as exc:
        raise DatabaseError(f"Database operation failed: {exc}", cause=exc) from exc
