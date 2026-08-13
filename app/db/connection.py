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


# Marker set on a physical connection once its pgvector adapters are registered.
# Registration is per-connection state, so this is the honest way to ask "has
# THIS connection been set up" without re-deriving it from the server.
_REGISTERED = "_handbook_pgvector_registered"


def _register_vector_once(conn: "psycopg.Connection") -> None:
    """Register pgvector type adapters on ``conn``, at most once per connection.

    Why the guard exists (a measured, site-wide latency bug)
    -------------------------------------------------------
    ``register_vector`` is not a local call: it does a ``TypeInfo.fetch`` against
    ``pg_type`` for each of vector/bit/halfvec/sparsevec, i.e. a **server round
    trip per type**. This used to run on EVERY ``get_connection()`` checkout in
    addition to the pool's ``configure`` hook, so every query in the app carried
    a burst of catalogue lookups in front of it. Measured against a real
    Postgres: 10 trivial ``SELECT 1`` calls through ``get_connection()`` issued
    **58** server round trips — a 5.8x amplification on every endpoint. That is
    invisible locally (sub-millisecond hops) and brutal on a cross-region
    deployment: at the ~250ms round trip measured between the API and its
    database, ~12 extra lookups is ~3s of latency *per connection checkout*,
    which is what made every page in the portal feel like it hung.

    The type OIDs cannot change under a live connection, so re-fetching them per
    checkout could never learn anything new. What the per-checkout call was
    actually defending against was a *stale* connection: one created before the
    ``vector`` extension existed, or surviving a ``docker compose down -v`` +
    re-init, which otherwise keeps dumping embeddings as bare ndarrays and
    fails with ``cannot adapt type 'ndarray'`` on ingest. That property is kept
    — a connection without the marker is still registered on checkout — it just
    no longer costs anything once the connection is set up. After deliberately
    recreating the extension under a running process, call ``close_pool()``
    (tests and scripts already do) so fresh connections re-register.

    Fails closed rather than swallowing a miss: a silently unregistered
    connection sitting in the pool is far worse than a loud error here.
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
    """Pool hook: run once when a new physical connection is opened."""
    _register_vector_once(conn)


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
            # Safety net for a connection that somehow reached a caller without
            # the pool's configure hook having registered it (e.g. opened before
            # the extension existed). A no-op once registered — see
            # _register_vector_once for why paying it per checkout was ~3s of
            # latency per query on a cross-region deploy.
            _register_vector_once(conn)
            yield conn
    except psycopg.Error as exc:
        raise DatabaseError(f"Database operation failed: {exc}", cause=exc) from exc
