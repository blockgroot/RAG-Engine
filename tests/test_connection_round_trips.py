"""Pin the number of SERVER round trips a pooled query actually costs.

The bug this exists to prevent (measured, site-wide)
---------------------------------------------------
``get_connection()`` called ``register_vector(conn)`` on every checkout, in
addition to the pool's ``configure`` hook. ``register_vector`` is not local: it
does a ``TypeInfo.fetch`` against ``pg_type`` per registered type
(vector/bit/halfvec/sparsevec), i.e. a **round trip each**. Measured against a
real Postgres, 10 trivial ``SELECT 1`` calls through ``get_connection()`` issued
**58** round trips — 5.8x amplification carried by every endpoint in the app.

Sub-millisecond locally, so no local test or timing would have noticed; ~3s per
checkout on a cross-region deploy (~250ms hops), which is what made every page
in the portal feel hung. Latency assertions would be flaky, so this counts round
trips instead — the thing that was actually wrong.
"""

from __future__ import annotations

import numpy as np
import psycopg

from app.db.connection import close_pool, get_connection, get_pool

from .conftest import requires_db


def _count_round_trips(fn) -> int:
    """Run ``fn`` and return how many statements were sent to the server.

    Counts at ``psycopg.Cursor.execute`` rather than ``Connection.execute``
    deliberately: ``TypeInfo.fetch`` uses its own cursor, so counting the
    connection-level helper misses precisely the calls under test (it reported a
    reassuring "1 per query" while 12 catalogue lookups went by unseen).
    """
    calls = []
    original = psycopg.Cursor.execute

    def counting(self, query, *args, **kwargs):
        calls.append(query)
        return original(self, query, *args, **kwargs)

    psycopg.Cursor.execute = counting
    try:
        fn()
    finally:
        psycopg.Cursor.execute = original
    return len(calls)


@requires_db
def test_a_pooled_query_costs_exactly_one_round_trip():
    """No hidden catalogue lookups in front of ordinary queries."""
    get_pool()
    # Warm every connection the pool will hand out, so this measures steady
    # state rather than one-off connection setup (which legitimately registers).
    for _ in range(30):
        with get_connection() as conn:
            conn.execute("SELECT 1")

    def ten_queries():
        for _ in range(10):
            with get_connection() as conn:
                conn.execute("SELECT 1")

    round_trips = _count_round_trips(ten_queries)
    # Exactly 10 is the correct answer; the guard allows a couple of connections
    # being opened mid-run, but nothing like the 58 this used to cost.
    assert round_trips <= 12, (
        f"{round_trips} round trips for 10 queries — something is issuing "
        "per-checkout server calls again (see _register_vector_once)"
    )


@requires_db
def test_pgvector_adapters_still_work_after_the_registration_guard():
    """The guard must not cost the property it guards: numpy still binds.

    An unregistered pooled connection fails with ``cannot adapt type 'ndarray'``
    on ingest, which is what the per-checkout call was defending against.
    """
    with get_connection() as conn:
        vector = np.array([0.1, 0.2, 0.3], dtype="float32")
        row = conn.execute("SELECT %s::vector", (vector,)).fetchone()
    assert row is not None and row[0] is not None


@requires_db
def test_a_fresh_pool_registers_before_the_first_query():
    """Registration happens on connection setup, not lazily on first bind."""
    close_pool()
    get_pool()
    with get_connection() as conn:
        vector = np.array([1.0, 0.0], dtype="float32")
        assert conn.execute("SELECT %s::vector", (vector,)).fetchone() is not None
