"""Charts come from the source the question asks about, and only from figures
that are really there.

- A question that NAMES a connector ("from Drive") is charted from that
  connector's tables only (a Notion page answered a Drive question).
- A measure binds to a column by its words or an abbreviation ("opex"), never
  by a nearby word ("Pro" is not profit).
- A figure read from prose is kept only when its clause holds that one figure
  and names it.
- When nothing stored answers, the closest unread document is read live.
- A Slack mention asking for a chart reaches Chart mode even in an indexed
  channel.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.agent import routing
from app.doctables.store import TableRef
from app.insights import coverage
from app.insights.resolve import AskIntent, ChartSpec

ORG = "00000000-0000-0000-0000-000000000001"
VIEWER = SimpleNamespace(is_unrestricted=False, acl=lambda: ["a@b.c"])


def _table(tid, provider, title, columns, doc=None):
    return TableRef(
        id=tid, name=title, columns=tuple(columns), row_count=4, truncated=False,
        notes=(), document_title=title, provider=provider, source_uri=None,
        document_id=doc or f"doc-{tid}",
    )


REVENUE_COLS = [
    {"key": "c0", "name": "Quarter", "type": "category", "samples": ["Q1", "Q2"]},
    {"key": "c1", "name": "Revenue (₹ lakh)", "type": "number", "unit": "₹ lakh"},
    {"key": "c2", "name": "Opex (₹ lakh)", "type": "number", "unit": "₹ lakh"},
]
NOTION = _table("t-notion", "notion", "Atlas Project Charter & Delivery", REVENUE_COLS)
DRIVE = _table("t-drive", "google", "FY26 Financials", REVENUE_COLS)


# --- the named connector decides ------------------------------------------------


def test_named_connector_filters_tables_before_ranking(monkeypatch):
    monkeypatch.setattr("app.doctables.store.list_tables", lambda **k: [NOTION, DRIVE])
    monkeypatch.setattr("app.insights.tables.document_similarity", lambda *a, **k: None)
    offered = routing._document_tables(ORG, None, VIEWER, "revenue from Drive", {"google"})
    assert [t.id for t in offered] == ["t-drive"]


def test_named_connector_drops_uploads(monkeypatch):
    monkeypatch.setattr(routing, "_upload_tables", lambda *a: ["upload-table"])
    monkeypatch.setattr(routing, "_document_tables", lambda *a: [DRIVE])
    assert routing._chartable_tables(ORG, None, VIEWER, "q", uploads=object(),
                                     named={"google"}) == [DRIVE]
    assert routing._chartable_tables(ORG, None, VIEWER, "q", uploads=object()) == [
        "upload-table", DRIVE]


def test_chart_mode_passes_the_named_connector(monkeypatch):
    seen = {}

    def tables(org_id, workspace_id, viewer, question, uploads=None, named=None):
        seen["named"] = named
        return [DRIVE]

    def classify(question, **kwargs):
        seen["providers"] = kwargs["providers"]
        return AskIntent("chart", spec=ChartSpec(metric="doc_table", group_by="c0",
                                                 period="quarter", chart="bar",
                                                 table_id="t-drive", value="c1"))

    monkeypatch.setattr(routing, "_chart_unsupported_model", lambda: None)
    monkeypatch.setattr(routing, "_chartable_tables", tables)
    monkeypatch.setattr(routing, "_chart_fields", lambda *a: None)
    monkeypatch.setattr(routing, "_chart_names", lambda *a: None)
    monkeypatch.setattr("app.insights.resolve.classify_question", classify)
    decision = routing._chart_mode_route(
        "Chart quarterly revenue from the Google Drive sheet",
        {"notion", "google", "linear"}, ORG, None, viewer=VIEWER,
    )
    assert decision.reason == "chart"
    assert seen["named"] == {"google"}
    assert set(seen["providers"]) <= {"google"}


def test_unread_documents_are_read_when_nothing_offered_answers(monkeypatch):
    calls = {"classify": 0}

    def classify(question, **kwargs):
        calls["classify"] += 1
        if kwargs["tables"] == ["fresh"]:
            return AskIntent("chart", spec=ChartSpec(metric="doc_table", group_by=None,
                                                     period="month", chart="bar",
                                                     table_id="fresh", value="c1"))
        return AskIntent("refuse", message="not here")

    listings = iter([[], ["fresh"]])
    monkeypatch.setattr(routing, "_chart_unsupported_model", lambda: None)
    monkeypatch.setattr(routing, "_chartable_tables", lambda *a, **k: next(listings))
    monkeypatch.setattr(routing, "_chart_fields", lambda *a: None)
    monkeypatch.setattr(routing, "_chart_names", lambda *a: None)
    monkeypatch.setattr(routing, "_read_unread_documents", lambda *a: True)
    monkeypatch.setattr("app.insights.resolve.classify_question", classify)
    decision = routing._chart_mode_route("chart revenue", {"google"}, ORG, None, viewer=VIEWER)
    assert decision.reason == "chart"
    assert calls["classify"] == 2


# --- binding a measure ------------------------------------------------------------


def test_abbreviation_binds_but_one_word_start_does_not():
    assert coverage.format_binds("operating expenses", "Opex (₹ lakh)")
    assert coverage.format_binds("profit and loss", "P&L")
    assert not coverage.format_binds("profit", "Pro")
    assert not coverage.format_binds("revenue", "Count")


def test_unbound_measure_names_the_closest_column():
    def similarity(a, b):
        return 0.6 if "Opex" in b else 0.1

    result = coverage.cover(
        "chart quarterly costs", [coverage.MeasureAsk("costs", "T1", "Revenue")],
        {"T1": DRIVE}, similarity=similarity, bind_min=0.70,
    )
    assert not result.bound
    assert result.missing == ('costs (closest column: Opex (₹ lakh) in "FY26 Financials")',)


def test_similarity_above_the_floor_binds():
    result = coverage.cover(
        "active customers by quarter",
        [coverage.MeasureAsk("active customers", "T1", "Revenue (₹ lakh)")],
        {"T1": DRIVE}, similarity=lambda a, b: 0.75, bind_min=0.70,
    )
    assert result.bound and result.bound[0].column_key == "c1"


# --- figures read from prose --------------------------------------------------------


DOC = (
    "Quarterly results.\n"
    "Revenue by quarter: Q1 revenue was ₹12L, Q2 grew to ₹15L.\n"
    "Operating expenses were ₹8L in Q1."
)


def test_a_clause_with_two_figures_is_not_trusted_for_either():
    from app.doctables.text_adapter import verify_rows

    rows = [{"cells": ["Q2", "₹12L"], "quote": "Q1 revenue was ₹12L, Q2 grew to ₹15L"}]
    assert verify_rows(DOC, rows, ["Quarter", "Revenue"]) == []


def test_one_figure_clause_with_its_context_is_kept():
    from app.doctables.text_adapter import verify_rows

    rows = [
        {"cells": ["Q1", "₹12L"], "quote": "Q1 revenue was ₹12L"},
        {"cells": ["Q2", "₹15L"], "quote": "Q2 grew to ₹15L",
         "context": "Revenue by quarter:"},
    ]
    kept = verify_rows(DOC, rows, ["Quarter", "Revenue"])
    assert [cells for cells, _ in kept] == [["Q1", "₹12L"], ["Q2", "₹15L"]]


def test_a_figure_under_the_wrong_measure_is_dropped():
    from app.doctables.text_adapter import verify_rows

    rows = [{"cells": ["Q1", "₹8L"], "quote": "Operating expenses were ₹8L in Q1"}]
    assert verify_rows(DOC, rows, ["Quarter", "Revenue"]) == []
    assert verify_rows(DOC, rows, ["Quarter", "Operating expenses"]) != []


def test_unreadable_reply_is_a_failure_to_retry():
    import pytest

    from app.core.exceptions import ProviderError
    from app.doctables.base import DocumentText
    from app.doctables.text_adapter import TextAdapter

    class Cut:
        def generate(self, prompt, **kwargs):
            return '{"tables": [{"name": "Rev'

    with pytest.raises(ProviderError):
        TextAdapter(llm=Cut()).extract(DocumentText("x", "t", DOC * 3))


# --- reading a document live --------------------------------------------------------


def test_read_now_stores_exact_and_prose_tables(monkeypatch):
    from app.doctables import ondemand

    saved = []
    page = (
        "| Quarter | Revenue |\n| --- | --- |\n| Q1 | 10 |\n| Q2 | 12 |\n\n" + DOC * 2
    )

    class Source:
        def fetch_document(self, external_id):
            return SimpleNamespace(title="FY26 Financials", content=page, tables=None)

    class LLM:
        def generate(self, prompt, **kwargs):
            return ('{"tables": [{"name": "Opex", "columns": ["Quarter", "Operating expenses"],'
                    ' "rows": [{"cells": ["Q1", "₹8L"], "quote": "Operating expenses were ₹8L in Q1"},'
                    ' {"cells": ["Q1", "₹8L"], "quote": "Operating expenses were ₹8L in Q1"}]}]}')

    monkeypatch.setattr("app.auth.credentials.get_live_connection_token", lambda *a, **k: "t")
    monkeypatch.setattr("app.auth.get_connection_config", lambda *a, **k: {})
    monkeypatch.setattr("app.sources.build_source_adapter", lambda *a, **k: Source())
    monkeypatch.setattr("app.llm.factory.build_llm_provider", lambda *a, **k: LLM())
    monkeypatch.setattr("app.doctables.store.replace_document_tables",
                        lambda doc_id, **k: saved.append((k.get("origin", "table"), k["tables"])))
    monkeypatch.setattr("app.doctables.store.mark_checked", lambda doc_id: None)
    monkeypatch.setattr(ondemand, "_mark_read", lambda *a, **k: None)
    doc = ondemand.Unread("doc-1", "google", "file-1", "FY26 Financials", 0.6)
    assert ondemand.read_now(doc, org_id=ORG, workspace_id=None) == 2
    assert [origin for origin, _ in saved] == ["table", "text"]


def test_read_now_never_raises(monkeypatch):
    from app.doctables import ondemand

    def boom(*a, **k):
        raise RuntimeError("reconnect")

    monkeypatch.setattr("app.auth.credentials.get_live_connection_token", boom)
    doc = ondemand.Unread("doc-1", "notion", "p-1", "Page", 0.6)
    assert ondemand.read_now(doc, org_id=ORG, workspace_id=None) == 0


def test_unread_documents_needs_a_real_viewer():
    from app.doctables import ondemand

    assert ondemand.unread_documents("q", org_id=ORG, workspace_id=None, viewer=None,
                                     providers=None, exclude_ids=set(), floor=0.3,
                                     limit=2) == []


# --- Slack --------------------------------------------------------------------------


def test_slack_chart_ask_in_an_indexed_channel_skips_the_slack_only_path(monkeypatch):
    from app.api import slack_events

    monkeypatch.setattr("app.insights.resolve.classify_route",
                        lambda q, **k: SimpleNamespace(chart_ask="visual"))
    assert slack_events._asks_for_chart("show revenue by quarter as a chart")
    monkeypatch.setattr("app.insights.resolve.classify_route",
                        lambda q, **k: SimpleNamespace(chart_ask=None))
    assert not slack_events._asks_for_chart("what did we decide about pricing?")
