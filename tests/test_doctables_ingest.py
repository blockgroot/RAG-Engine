"""Figures in sentences are read AT INGEST, inline.

Pinned: a page with figures is charted as soon as it is ingested (no queue,
no tick); the paced background model is used, never a person's quota; a page
without figures costs no call; a page whose sentences hold no series is
marked read so Chart mode does not pay again; a failed read never costs the
document or the tables it already has; and the text pass never touches the
tables ingestion stored exactly.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.db import get_connection
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


@pytest.fixture
def llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.delenv("DOCTABLES_TEXT_ENABLED", raising=False)  # on by default
    monkeypatch.setattr("app.llm.factory.build_aux_llm_provider", lambda *a, **k: fake)
    monkeypatch.setattr("app.llm.pacing.wait_for_background_slot", lambda *a, **k: True)
    return fake


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


def _ingest(org_id, content=PROSE):
    doc_id = _document(org_id)
    _store_tables(doc_id, SourceDocument(external_id="p", title="Quarterly update",
                                         content=content),
                  org_id=org_id, workspace_id=None)
    return doc_id


def _marked(doc_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT status FROM doc_text_queue WHERE document_id = %s", (doc_id,),
        ).fetchone()
    return row[0] if row else None


def _tables(org):
    return {t.origin: t for t in
            table_store.list_tables(org_id=org, workspace_id=None, viewer=VIEWER)}


def test_ingest_reads_the_figures_and_keeps_the_exact_table(org, llm):
    _ingest(org)
    assert llm.calls == 1
    tables = _tables(org)
    assert set(tables) == {"table", "text"}
    assert tables["table"].name == "Budget"
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
    assert {p["group"]: p["value"] for p in panel["points"]} == {
        "Q1": 1200000, "Q2": 1500000, "Q3": 1800000}


def test_a_page_without_figures_costs_no_call(org, llm):
    _ingest(org, "Leave policy. Ask your manager before booking time off.")
    assert llm.calls == 0


def test_a_page_whose_sentences_hold_no_series_is_marked_read(org, llm):
    llm.reply = {"tables": []}
    doc_id = _ingest(org)
    assert _marked(doc_id) == "done"
    # Chart mode skips a page already read (matching_prose excludes it).
    assert table_store.matching_prose(
        org_id=org, workspace_id=None, viewer=VIEWER, question="revenue") == []


def test_a_failed_read_keeps_the_document_and_its_exact_table(org, llm):
    llm.reply = RuntimeError("429")
    doc_id = _ingest(org)
    assert set(_tables(org)) == {"table"}
    assert _marked(doc_id) is None  # not marked: Chart mode may still read it


def test_switched_off_reads_nothing(org, llm, monkeypatch):
    monkeypatch.setenv("DOCTABLES_TEXT_ENABLED", "false")
    _ingest(org)
    assert llm.calls == 0 and set(_tables(org)) == {"table"}


def test_a_reingest_starts_over(org, llm):
    """A re-ingest replaces the documents row, so its text tables and its
    read mark cascade away and the new row is read afresh."""
    doc_id = _ingest(org)
    with get_connection() as conn:
        conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
        conn.commit()
    assert _marked(doc_id) is None
    assert table_store.list_tables(org_id=org, workspace_id=None, viewer=VIEWER) == []
