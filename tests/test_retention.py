"""Retention rules are valid SQL on the real schema, and one bad rule never stops the rest."""

from __future__ import annotations

import pytest

from app.jobs import retention


def _sql(table: str, where: str) -> str:
    return (f"WITH doomed AS (SELECT ctid FROM {table} WHERE {where} LIMIT {retention.BATCH}) "
            f"DELETE FROM {table} WHERE ctid IN (SELECT ctid FROM doomed)")


@pytest.mark.parametrize("table,where", retention.RULES)
def test_every_rule_plans_on_the_real_schema(table, where):
    from app.db.connection import get_connection

    # EXPLAIN plans the DELETE without running it: a wrong column or table fails here.
    with get_connection() as conn:
        conn.execute("EXPLAIN " + _sql(table, where))


def test_kept_on_purpose_is_never_a_target():
    tables = {t for t, _ in retention.RULES}
    assert not tables & {"documents", "chunks", "activity_facts", "doc_tables", "users",
                         "oauth_connections", "schedulers", "organizations"}


def test_one_failing_rule_never_stops_the_rest(monkeypatch):
    ran: list[str] = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql):
            table = sql.split("FROM ")[1].split()[0]
            ran.append(table)
            if table == "oauth_states":
                raise RuntimeError("lock timeout")
            return type("Cur", (), {"rowcount": 2})()

    monkeypatch.setattr(retention, "get_connection", lambda: Conn())
    out = retention.purge_expired()
    assert len(ran) == len(retention.RULES)
    assert "oauth_states" not in out and out["actions"] == 4
