"""Background figures: ingestion flags, the tick reads.

Pinned: ingestion never calls a model for charts (it only flags a document
after a no-AI check); the tick reads a bounded batch; a model failure is
retried up to the cap and then given up on; a busy background budget does not
count against the document; the text pass never touches the tables ingestion
stored; and nothing happens at all while the text adapter is switched off.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.config.settings import DocTablesSettings
from app.db import get_connection
from app.doctables import extract, queue
from app.doctables import store as table_store
from app.ingestion.pipeline import _store_tables
from app.sources.base import SourceDocument
from app.vectorstore.base import Viewer
from .conftest import requires_db

pytestmark = requires_db

PROSE = (
    "Q1 revenue was ₹12L, which was below plan. In Q2 revenue grew to ₹15L "
    "after the launch. Q3 revenue reached ₹18 lakh.\n\n"
    "## Budget\n\n| Team | Budget |\n|---|---|\n| Core | 10 |\n| Growth | 5 |\n"
)
REPLY = {"tables": [{"name": "Revenue by quarter", "columns": ["Quarter", "Revenue"], "rows": [
    {"cells": ["Q1", "₹12L"], "quote": "Q1 revenue was ₹12L"},
    {"cells": ["Q2", "₹15L"], "quote": "In Q2 revenue grew to ₹15L"},
    {"cells": ["Q3", "₹18 lakh"], "quote": "Q3 revenue reached ₹18 lakh"},
]}]}
ON = DocTablesSettings(text_enabled=True, text_batch=5, text_max_attempts=2)
VIEWER = Viewer(email="sana@acme.test")


class FakeLLM:
    def __init__(self, reply=REPLY):
        self.reply, self.calls = reply, 0

    def generate(self, prompt, *, max_tokens=None):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return json.dumps(self.reply)


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"figures-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _document(org_id, title="Quarterly update"):
    with get_connection() as conn:
        row = conn.execute(
            """INSERT INTO documents (org_id, title, source_uri, source_provider,
                   source_external_id) VALUES (%s, %s, 'https://notion.test/p', 'notion', %s)
               RETURNING id""",
            (org_id, title, uuid.uuid4().hex),
        ).fetchone()
        conn.commit()
    return str(row[0])


def _ingest(org_id, content=PROSE, monkeypatch=None):
    doc_id = _document(org_id)
    _store_tables(doc_id, SourceDocument(external_id="p", title="Quarterly update",
                                         content=content),
                  org_id=org_id, workspace_id=None)
    return doc_id


def _queue_row(doc_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT status, attempts, text IS NOT NULL FROM doc_text_queue "
            "WHERE document_id = %s", (doc_id,),
        ).fetchone()


@pytest.fixture
def text_on(monkeypatch):
    monkeypatch.setenv("DOCTABLES_TEXT_ENABLED", "true")


def test_ingestion_only_flags_and_never_calls_a_model(org, text_on, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ingestion must not call a model for charts")
    monkeypatch.setattr("app.doctables.text_adapter.TextAdapter.extract", boom)
    doc_id = _ingest(org)
    assert _queue_row(doc_id) == ("pending", 0, True)
    # The pipe table was stored at once by the table adapter, exactly.
    [table] = table_store.list_tables(org_id=org, workspace_id=None, viewer=VIEWER)
    assert (table.name, table.origin) == ("Budget", "table")


def test_a_document_without_figures_is_not_queued(org, text_on):
    doc_id = _ingest(org, "Leave policy. Ask your manager before booking time off.")
    assert _queue_row(doc_id) is None


def test_nothing_is_queued_while_the_text_adapter_is_off(org, monkeypatch):
    monkeypatch.delenv("DOCTABLES_TEXT_ENABLED", raising=False)
    assert _queue_row(_ingest(org)) is None
    assert queue.run_pending(DocTablesSettings(text_enabled=False)) == 0


def test_the_tick_reads_figures_and_keeps_the_ingested_table(org, text_on):
    doc_id = _ingest(org)
    llm = FakeLLM()
    assert queue.run_pending(ON, llm=llm, wait_for_slot=lambda: True) == 1
    assert llm.calls == 1
    assert _queue_row(doc_id) == ("done", 1, False)  # text cleared once done
    tables = {t.origin: t for t in
              table_store.list_tables(org_id=org, workspace_id=None, viewer=VIEWER)}
    assert set(tables) == {"table", "text"}
    text = tables["text"]
    rows = table_store.list_rows(text)
    assert [cells["c1"] for cells, _, _ in rows] == [1200000, 1500000, 1800000]
    assert rows[1][2] == "In Q2 revenue grew to ₹15L"
    # The chart says where its numbers came from, and the hover quotes them.
    from app.agent.insights_agent import run_table_spec
    from app.insights.resolve import TABLE_METRIC, ChartSpec

    spec = ChartSpec(metric=TABLE_METRIC, group_by="c0", period="month", chart="bar",
                     measure="sum", value="c1", table_id=text.id)
    panel, _ = run_table_spec(spec, org_id=org, workspace_id=None, viewer=VIEWER)
    assert panel["title"].endswith("(taken from text)")
    assert "read from sentences" in panel["caveat"]
    assert panel["details"][0]["subject"] == "“Q1 revenue was ₹12L”"
    assert {p["group"]: p["value"] for p in panel["points"]} == {
        "Q1": 1200000, "Q2": 1500000, "Q3": 1800000}
    # A second tick has nothing left to do.
    assert queue.run_pending(ON, llm=llm, wait_for_slot=lambda: True) == 0
    assert llm.calls == 1


def test_a_failing_document_is_retried_then_given_up_on(org, text_on):
    doc_id = _ingest(org)
    llm = FakeLLM(RuntimeError("429"))
    with get_connection() as conn:  # make the claim immediately re-claimable
        conn.execute("UPDATE doc_text_queue SET claimed_at = NULL")
        conn.commit()
    queue.run_pending(ON, llm=llm, wait_for_slot=lambda: True)
    assert _queue_row(doc_id)[:2] == ("pending", 1)
    queue.run_pending(ON, llm=llm, wait_for_slot=lambda: True)
    assert _queue_row(doc_id) == ("failed", 2, False)
    assert llm.calls == 2


def test_a_busy_background_budget_does_not_cost_the_document_an_attempt(org, text_on):
    doc_id = _ingest(org)
    llm = FakeLLM()
    queue.run_pending(ON, llm=llm, wait_for_slot=lambda: False)
    assert llm.calls == 0
    assert _queue_row(doc_id)[:2] == ("pending", 0)


def test_a_batch_is_bounded(org, text_on):
    for _ in range(4):
        _ingest(org)
    llm = FakeLLM()
    small = DocTablesSettings(text_enabled=True, text_batch=2)
    assert queue.run_pending(small, llm=llm, wait_for_slot=lambda: True) == 2
    assert llm.calls == 2


def test_a_reingest_starts_over(org, text_on):
    """A re-ingest replaces the documents row, so its queue row and its text
    tables cascade away and the new row is flagged afresh."""
    doc_id = _ingest(org)
    queue.run_pending(ON, llm=FakeLLM(), wait_for_slot=lambda: True)
    with get_connection() as conn:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        conn.commit()
    assert _queue_row(doc_id) is None
    assert table_store.list_tables(org_id=org, workspace_id=None, viewer=VIEWER) == []
