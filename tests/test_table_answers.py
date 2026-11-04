"""Sales-sheet questions that failed end to end (evaluation/sales_e2e, 3 Nov 2026).

Pinned here, one per root cause:

- a figure written with its scale ("₹32.26 lakh") names the scale once, not
  again on its column ("3,226,000 lakh");
- the chart model is shown every value of a small column ("Won" was cut);
- a TABLE pick is drawn even when the offered tables span two connectors;
- a column the question names is the breakdown, not a date axis it never asked
  for; one typed value of a column is a filter, two are a comparison;
- a refused answer is asked once more on the next tool that cleared the gate,
  never on the tool that refused, and never when the asker chose the tool;
- a table row the question names by a cell is found, as that viewer only,
  and routes Ask to that sheet's tool;
- one word of a longer title does not name a page.
"""

from __future__ import annotations

import re
import uuid

import pytest

from app.agent.routing import RoutingDecision, refusal_retry
from app.db import get_connection
from app.doctables import extract, store as table_store
from app.doctables.store import TableRef
from app.doctables.text_adapter import facts_to_tables
from app.ingestion.pipeline import _store_tables
from app.insights import resolve
from app.insights import tables as doc_tables
from app.insights.resolve import AskIntent, ChartSpec, TABLE_METRIC
from app.sources.base import SourceDocument
from app.vectorstore.base import Viewer

from .conftest import requires_db

LEADS = (
    "Created,ID,Owner,Stage,Region,Deal Value\n"
    "2026-06-11,L-1001,Rohan Mehta,Lost,West,511000\n"
    "2026-06-16,L-1007,Kavya Nair,Won,South,980000\n"
    "2026-06-08,L-1008,Kavya Nair,Lost,South,1486000\n"
    "2026-06-01,L-1011,Kavya Nair,Won,South,1206000\n"
    "2026-06-15,L-1013,Arjun Iyer,New,South,554000\n"
)


def _leads(provider: str = "google") -> TableRef:
    table = extract.profile(extract.parse_csv(LEADS, name="Leads"))
    return TableRef(
        id=uuid.uuid4().hex, name="Leads", columns=tuple(c.as_dict() for c in table.columns),
        row_count=len(table.cells), truncated=False, notes=(), document_title="Leads",
        provider=provider, source_uri=None, document_id=uuid.uuid4().hex,
    )


def _col(table: TableRef, name: str) -> str:
    return next(c["key"] for c in table.columns if c["name"] == name)


# ---------------------------------------------------------------------------
# Figures read from prose
# ---------------------------------------------------------------------------


def test_a_scaled_figure_names_its_scale_once():
    prose = ("In July, the team booked ₹32.26 lakh in revenue.\n\n"
             "In August, the team booked ₹37.44 lakh in revenue.")
    facts = [{"measure": "revenue", "unit": "lakh", "period": month, "value": value,
              "quote": f"In {month}, the team booked {value} in revenue."}
             for month, value in (("July", "₹32.26 lakh"), ("August", "₹37.44 lakh"))]
    [table] = facts_to_tables(prose, facts)
    revenue = table.columns[-1]
    assert (revenue.name, revenue.unit) == ("Revenue", "₹")
    assert [row["c1"] for row in table.cells] == [3226000.0, 3744000.0]


def test_an_unscaled_figure_keeps_its_unit_on_the_column():
    prose = "In July, Plant A made 420 kg.\n\nIn August, Plant A made 380 kg."
    facts = [{"measure": "output", "unit": "kg", "period": month, "value": value,
              "quote": f"In {month}, Plant A made {value}."}
             for month, value in (("July", "420 kg"), ("August", "380 kg"))]
    [table] = facts_to_tables(prose, facts)
    assert table.columns[-1].name == "Output (kg)"


# ---------------------------------------------------------------------------
# What the chart model is shown, and what is drawn
# ---------------------------------------------------------------------------


def test_every_value_of_a_small_column_is_shown_to_the_model():
    line = doc_tables.catalogue([_leads()])
    assert "Stage (category, values: Lost, New, Won)" in line


def test_a_table_pick_is_drawn_when_the_tables_span_two_connectors():
    leads, other = _leads("google"), _leads("notion")
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(leads, "Stage"), period="month",
                     chart="bar", measure="count", table_id=leads.id)
    intent = resolve._align_connector(AskIntent("chart", spec=spec), "Leads by stage",
                                      [], {"T1": leads, "T2": other}, None, None)
    assert intent.kind == "chart" and intent.spec.table_id == leads.id


def _picked(question: str, reply: str, table: TableRef) -> ChartSpec:
    created = _col(table, "Created")
    spec = ChartSpec(metric=TABLE_METRIC, group_by=created, period="month", chart="bar",
                     measure="count", table_id=table.id)
    handles = {"T1": table}
    intent = resolve._table_period_axis(AskIntent("chart", spec=spec), question, reply, handles)
    return resolve._typed_values(intent, question, handles).spec


def test_the_named_column_is_the_breakdown_not_a_default_date():
    leads = _leads()
    spec = _picked("Leads by stage", '{"group_by": null, "period": "month"}', leads)
    assert (spec.group_by, spec.split_by) == (_col(leads, "Stage"), None)


def test_a_period_the_asker_typed_stays_the_axis():
    leads = _leads()
    spec = _picked("Leads by stage per month", '{"group_by": null, "period": "month"}', leads)
    assert (spec.group_by, spec.split_by) == (_col(leads, "Created"), _col(leads, "Stage"))


def test_one_typed_value_is_a_filter_and_two_are_a_comparison():
    leads = _leads()
    owner = _col(leads, "Owner")
    one = _picked("Lead count by region for Rohan Mehta", '{"group_by": "Region"}', leads)
    assert (owner, "Rohan Mehta") in one.filters
    two = _picked("Kavya Nair vs Rohan Mehta by region", '{"group_by": "Region"}', leads)
    assert all(key != owner for key, _ in two.filters)


TARGETS = ("Employee,Month,Revenue Target\n"
           "Kavya Nair,2026-06-01,900000\nRohan Mehta,2026-06-01,770000\n")


def _targets() -> TableRef:
    table = extract.profile(extract.parse_csv(TARGETS, name="Targets"))
    return TableRef(
        id=uuid.uuid4().hex, name="Targets", columns=tuple(c.as_dict() for c in table.columns),
        row_count=len(table.cells), truncated=False, notes=(), document_title="Targets",
        provider="google", source_uri=None, document_id=uuid.uuid4().hex,
    )


def test_a_period_asked_puts_the_date_column_on_the_axis_never_in_the_series():
    """Staging: "... by month" on a table whose first column is a category drew
    Employee along the axis and the Month as a series, then refused it."""
    targets = _targets()
    employee, month = _col(targets, "Employee"), _col(targets, "Month")
    spec = ChartSpec(metric=TABLE_METRIC, group_by=employee, period="month", chart="bar",
                     measure="sum", value=_col(targets, "Revenue Target"), table_id=targets.id)
    intent = resolve._table_period_axis(AskIntent("chart", spec=spec), "Revenue target by month",
                                        '{"group_by": null, "period": "month"}', {"T1": targets})
    assert (intent.spec.group_by, intent.spec.split_by) == (month, employee)


@pytest.mark.parametrize("reply, period", [
    ('{"period": "day"}', "month"),                        # never asked: the default grain
    ('{"period": "day", "period_words": "daily"}', "day"),  # their own word for it
])
def test_a_period_is_kept_only_when_the_asker_asked_for_it(reply, period):
    leads = _leads()
    question = "leads by rohan in august" if "daily" not in reply else "daily leads by rohan"
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(leads, "Created"), period="day",
                     chart="bar", measure="count", table_id=leads.id)
    intent = resolve._table_period_axis(AskIntent("chart", spec=spec), question, reply,
                                        {"T1": leads})
    assert intent.spec.period == period


def test_several_kept_values_are_drawn_as_one_series_each():
    leads = _leads()
    owner, created = _col(leads, "Owner"), _col(leads, "Created")
    spec = ChartSpec(metric=TABLE_METRIC, group_by=created, period="month", chart="bar",
                     measure="sum", value=_col(leads, "Deal Value"), table_id=leads.id,
                     filters=((owner, ("Kavya Nair", "Rohan Mehta")),))
    intent = resolve._table_period_axis(
        AskIntent("chart", spec=spec), "Compare Kavya Nair and Rohan Mehta revenue by month",
        '{"period": "month"}', {"T1": leads})
    assert (intent.spec.group_by, intent.spec.split_by) == (created, owner)


def test_a_breakdown_matched_to_no_column_is_refused_not_drawn_by_date():
    """Staging, Claude Haiku: "won revenue by employee" came back with no
    column and was drawn by Created month."""
    leads = _leads()
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(leads, "Created"), period="month",
                     chart="bar", measure="sum", value=_col(leads, "Deal Value"),
                     table_id=leads.id)
    intent = resolve._table_period_axis(
        AskIntent("chart", spec=spec), "Show won revenue by employee",
        '{"group_by": null, "breakdown_words": "by employee"}', {"T1": leads})
    assert intent.kind == "refuse" and "Owner" in intent.message


def test_a_filter_may_keep_several_values_and_survives_the_round_trip():
    leads = _leads()
    pick = doc_tables.parse_pick({"table": "T1", "value": "Deal Value", "group_by": "Created",
                                  "filters": {"Owner": ["Kavya Nair", "Rohan Mehta"]}},
                                 {"T1": leads})
    assert pick.filters == ((_col(leads, "Owner"), ("Kavya Nair", "Rohan Mehta")),)
    spec = ChartSpec(metric=TABLE_METRIC, group_by=pick.group_by, period="month", chart="bar",
                     measure="sum", value=pick.value, filters=pick.filters, table_id=leads.id)
    assert resolve.spec_from_dict(resolve.spec_to_dict(spec)) == spec
    # A header the table lacks, with values only one column holds.
    pick = doc_tables.parse_pick({"table": "T1", "value": "Deal Value",
                                  "filters": {"Employee": ["Kavya Nair", "Rohan Mehta"]}},
                                 {"T1": leads})
    assert pick.filters == ((_col(leads, "Owner"), ("Kavya Nair", "Rohan Mehta")),)
    # The same list written as one string.
    pick = doc_tables.parse_pick({"table": "T1", "filters": {"Owner": "Kavya Nair, Rohan Mehta"}},
                                 {"T1": leads})
    assert pick.filters == ((_col(leads, "Owner"), ("Kavya Nair", "Rohan Mehta")),)
    with pytest.raises(doc_tables.TableRefusal):
        doc_tables.parse_pick({"table": "T1", "filters": {"Employee": "Priya Sharma"}},
                              {"T1": leads})


# ---------------------------------------------------------------------------
# A refusal is asked once more, elsewhere
# ---------------------------------------------------------------------------


def test_a_refusal_is_retried_on_the_next_tool_that_cleared_the_gate():
    decision = RoutingDecision("slack", "best-match",
                               {"slack": 0.536, "notion": 0.520, "google": 0.449, "linear": 0.2})
    assert refusal_retry(decision) == ("notion", {"google"})


@pytest.mark.parametrize("reason", ["requested", "tool-named", "graph-named", "weak-best-match"])
def test_a_tool_the_asker_chose_is_never_second_guessed(reason):
    decision = RoutingDecision("slack", reason, {"slack": 0.5, "google": 0.45})
    assert refusal_retry(decision) is None


def test_nothing_else_cleared_the_gate_so_the_refusal_stands():
    assert refusal_retry(RoutingDecision("notion", "best-match", {"notion": 0.5, "google": 0.2})) is None


def _routed(monkeypatch, rows_tool, scores):
    from app.agent import routing

    monkeypatch.setattr(routing, "_connected_providers", lambda *a, **k: set(scores))
    monkeypatch.setattr(routing, "_probe_scores", lambda *a, **k: scores)
    monkeypatch.setattr(routing, "_try_question_route", lambda *a, **k: None)
    monkeypatch.setattr(routing, "_rows_named_provider", lambda *a, **k: rows_tool)
    return routing.choose_agent("Compare Kavya Nair and Rohan Mehta", "org",
                                viewer=Viewer(email="a@x.com"))


def test_a_question_naming_a_sheet_s_cells_is_routed_to_that_sheet(monkeypatch):
    """Staging: Slack 0.489, Notion 0.486, Drive 0.459 -- the sheet's rows
    name both people, but a sheet embeds only its columns."""
    decision = _routed(monkeypatch, "google", {"slack": 0.489, "notion": 0.486, "google": 0.459})
    assert (decision.agent_key, decision.reason) == ("google", "rows-named")
    # A refusal there is still asked again elsewhere.
    assert refusal_retry(decision) == ("slack", {"notion"})


@pytest.mark.parametrize("rows_tool", [None, "linear"])
def test_no_single_sheet_or_one_below_the_gate_leaves_the_probe_s_pick(monkeypatch, rows_tool):
    decision = _routed(monkeypatch, rows_tool, {"slack": 0.489, "google": 0.459, "linear": 0.2})
    assert (decision.agent_key, decision.reason) == ("slack", "best-match")


# ---------------------------------------------------------------------------
# A row named by a cell (DB)
# ---------------------------------------------------------------------------


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute("INSERT INTO organizations (name) VALUES (%s) RETURNING id",
                           (f"rows-{uuid.uuid4().hex[:8]}",)).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _sheet(org_id: str, viewers: list[str]) -> str:
    with get_connection() as conn:
        doc = conn.execute(
            """
            INSERT INTO documents (org_id, title, source_uri, source_provider,
                                   source_external_id, doc_is_public, doc_viewers)
            VALUES (%s, 'Leads', 'https://drive.test/x', 'google', %s, false, %s)
            RETURNING id
            """,
            (org_id, uuid.uuid4().hex, viewers),
        ).fetchone()
        conn.commit()
    raw = extract.parse_csv(LEADS, name="Leads")
    _store_tables(str(doc[0]), SourceDocument(external_id="x", title="Leads", content="",
                                              tables=[raw]), org_id=org_id, workspace_id=None)
    return str(doc[0])


@requires_db
def test_a_row_named_by_a_cell_is_found_for_its_viewer_only(org):
    _sheet(org, ["sana@acme.test"])
    ask = "Who owned L-1008?"

    def rows(viewer):
        return table_store.rows_naming(ask, org_id=org, workspace_id=None, viewer=viewer,
                                       providers=["google"])

    [row] = rows(Viewer(email="sana@acme.test"))
    assert "ID: L-1008" in row.text and "Owner: Kavya Nair" in row.text
    assert rows(Viewer(email="someone@acme.test")) == []
    assert rows(Viewer.public_only_viewer()) == []
    assert rows(None) == []


@requires_db
def test_a_value_shared_by_many_rows_names_none_of_them(org):
    _sheet(org, ["sana@acme.test"])
    hits = table_store.rows_naming("Kavya Nair's leads", org_id=org, workspace_id=None,
                                   viewer=Viewer(email="sana@acme.test"), providers=["google"])
    # Three rows is still a handful: only a value naming MORE than
    # MAX_LOOKUP_ROWS rows is a group. Here it names exactly hers.
    assert {re.search(r"ID: (L-\d+)", h.text).group(1) for h in hits} == {
        "L-1007", "L-1008", "L-1011"}


# ---------------------------------------------------------------------------
# Which found rows are the ones asked about (no DB)
# ---------------------------------------------------------------------------

_LEAD_COLS = [{"name": n} for n in ("Lead ID", "Owner", "Field Officer", "Company")]
_TEAM_COLS = [{"name": n} for n in ("Employee", "Role")]
_TARGET_COLS = [{"name": n} for n in ("Employee", "Month", "Target")]


def _found(table: str, row_no: int, cols, cells) -> tuple:
    return ("doc", table, "google", None, table, row_no, cols, list(cells), False, table)


def _picked_rows(question: str, found: list[tuple]) -> list[str]:
    return [h.text for h in table_store.pick_rows(question, found)]


def test_the_row_a_question_names_outranks_rows_sharing_common_words():
    found = [_found("Team", i, _TEAM_COLS, (name, "Field Officer"))
             for i, name in enumerate(("Divya Menon", "Farhan Qureshi", "Harpreet Gill"))]
    found.append(_found("Leads", 9, _LEAD_COLS, ("L-1008", "Kavya Nair", "Divya Menon", "Kesari")))
    assert "L-1008" in _picked_rows("Who was the field officer on L-1008?", found)[0]


def test_a_follow_up_s_other_words_lift_its_row_above_the_cap():
    """"What is the target for October for Kavya Nair, the owner of lead L-1076
    (Indigo Labs)?" -- her October target row ranked ninth of nine and was cut."""
    months = ("June 2026", "July 2026", "August 2026", "September 2026", "October 2026")
    found = [_found("Targets", i, _TARGET_COLS, ("Kavya Nair", m, "900000"))
             for i, m in enumerate(months)]
    found += [_found("Leads", 1, _LEAD_COLS, ("L-1076", "Kavya Nair", "Divya Menon", "Indigo Labs")),
              _found("Leads", 2, _LEAD_COLS, ("L-1139", "Rohan Mehta", "Farhan Qureshi", "Indigo Labs")),
              _found("Leads", 3, _LEAD_COLS, ("L-1201", "Rohan Mehta", "Farhan Qureshi", "Indigo Labs")),
              _found("Team", 4, _TEAM_COLS, ("Kavya Nair", "Sales Executive"))]
    rows = _picked_rows("What is the target for October for Kavya Nair, the owner of "
                        "lead L-1076 (Indigo Labs)?", found)
    assert "L-1076" in rows[0]
    assert "October 2026" in rows[1]


def test_a_value_on_more_rows_than_the_cap_names_none_of_them():
    found = [_found("Leads", i, _LEAD_COLS, (f"L-{i}", "Rohan Mehta", "Farhan Qureshi", "X"))
             for i in range(table_store.MAX_LOOKUP_ROWS + 1)]
    assert _picked_rows("How did Rohan Mehta do?", found) == []


# ---------------------------------------------------------------------------
# A figure worked out over a sheet's rows (insights/table_answers.py)
# ---------------------------------------------------------------------------

import json  # noqa: E402

from app.insights import table_answers  # noqa: E402
from app.insights.resolve import _route_prompt, classify_route  # noqa: E402
from app.rag.retrieval import HybridRetriever  # noqa: E402
from app.vectorstore.base import RetrievedChunk  # noqa: E402


class _Form:
    """A model that fills the form with ``queries`` and records the prompt."""

    model = "test-model"
    last_usage = None

    def __init__(self, queries):
        self._reply = json.dumps({"queries": queries})
        self.prompts: list[str] = []

    def generate(self, prompt, **_k):
        self.prompts.append(prompt)
        return self._reply


def _sales_tables(org):
    _sheet(org, ["sana@acme.test"])
    return table_store.list_tables(org_id=org, workspace_id=None,
                                   viewer=Viewer(email="sana@acme.test"))


def _worked(org, *queries) -> str:
    out = table_answers.answer("q?", _sales_tables(org), llm=_Form(list(queries)))
    return "\n".join(a.text for a in out)


@requires_db
def test_a_ratio_is_worked_out_by_sql_for_each_reading(org):
    text = _worked(
        org,
        {"table": "T1", "operation": "ratio", "of_these": {"Stage": "Won"}, "label": "of all"},
        {"table": "T1", "operation": "ratio", "filters": {"Stage": ["Won", "Lost"]},
         "of_these": {"Stage": "Won"}, "label": "of closed"},
    )
    assert "of all" in text and "2 of 5 = 40.0%" in text
    assert "of closed" in text and "2 of 4 = 50.0%" in text


@requires_db
def test_a_ratio_that_narrows_nothing_is_dropped_not_100_percent(org):
    assert _worked(org, {"table": "T1", "operation": "ratio", "filters": {"Stage": "Won"},
                         "of_these": {"Stage": "Won"}}) == ""


@requires_db
def test_a_value_the_rows_do_not_hold_is_dropped_not_zero(org):
    assert _worked(org, {"table": "T1", "operation": "count",
                         "filters": {"Owner": "Priya Sharma"}}) == ""


@requires_db
def test_a_column_the_table_does_not_have_is_dropped(org):
    assert _worked(org, {"table": "T1", "operation": "sum", "value": "Salary"}) == ""


@requires_db
def test_a_chart_filtered_to_two_people_counts_only_their_rows(org):
    from app.agent.insights_agent import run_table_spec

    [leads] = _sales_tables(org)
    owner, value = _col(leads, "Owner"), _col(leads, "Deal Value")
    spec = ChartSpec(metric=TABLE_METRIC, group_by=owner, period="month", chart="bar",
                     measure="sum", value=value, table_id=leads.id,
                     filters=((owner, ("kavya nair", "Rohan Mehta")),))
    panel, _ = run_table_spec(spec, org_id=org, workspace_id=None,
                              viewer=Viewer(email="sana@acme.test"))
    assert {p["group"]: p["value"] for p in panel["points"]} == {
        "Kavya Nair": 3672000.0, "Rohan Mehta": 511000.0}
    assert "Owner: Kavya Nair, Rohan Mehta" in panel["title"]


@requires_db
def test_the_top_row_and_a_grouped_total_largest_first(org):
    text = _worked(
        org,
        {"table": "T1", "operation": "top", "value": "Deal Value", "label": "biggest"},
        {"table": "T1", "operation": "sum", "value": "Deal Value", "group_by": "Owner",
         "filters": {"Stage": "Won"}, "label": "won by owner"},
    )
    biggest = next(line for line in text.splitlines() if "biggest" in line)
    assert biggest.index("L-1008") < biggest.index("L-1011")
    assert "Kavya Nair: 2,186,000" in text  # 980,000 + 1,206,000, the Won rows only


def test_the_question_check_is_unchanged_when_table_answers_are_off():
    assert "table:" not in _route_prompt("q?", github=False)
    assert '"table": true|false' in _route_prompt("q?", github=False, table=True)
    class _Says:
        def generate(self, prompt, **_k):
            return '{"chart": null, "table": true}'

    on = classify_route("q?", github=False, table=True, llm=_Says())
    off = classify_route("q?", github=False, table=False, llm=_Says())
    assert (on.table_ask, off.table_ask) == (True, False)


class _NoHits:
    """A store whose viewer-filtered query finds nothing: the asker cannot open the sheet."""

    def query(self, *a, **k):
        return []


def test_a_figure_from_a_sheet_the_asker_cannot_open_is_dropped_at_run_time(monkeypatch):
    figure = table_answers.TableAnswer(provider="google", document_id="d1",
                                       document_title="Leads", source_uri=None, text="25 of 164")
    monkeypatch.setattr("app.doctables.store.rows_naming", lambda *a, **k: [])
    retriever = HybridRetriever(store=_NoHits(), reranker=None)
    hits = retriever._table_rows("org", "q?", [0.0], workspace_id=None,
                                 viewer=Viewer(email="x@acme.test"), tools=["google"],
                                 worked=(figure,))
    assert hits == []


def test_a_figure_the_asker_may_read_is_a_passage_of_its_sheet(monkeypatch):
    figure = table_answers.TableAnswer(provider="google", document_id="d1",
                                       document_title="Leads", source_uri=None, text="25 of 164")

    class _Sheet:
        def query(self, *a, **k):
            return [RetrievedChunk(content="cols", score=0.41, document_id="d1", chunk_index=0,
                                   org_id="org", doc_is_public=False)]

    monkeypatch.setattr("app.doctables.store.rows_naming", lambda *a, **k: [])
    retriever = HybridRetriever(store=_Sheet(), reranker=None)
    [hit] = retriever._table_rows("org", "q?", [0.0], workspace_id=None,
                                  viewer=Viewer(email="x@acme.test"), tools=["google"],
                                  worked=(figure,))
    # The document's own cosine, never a made-up score; private stays private.
    assert (hit.content, hit.score, hit.document_id, hit.doc_is_public) == (
        "25 of 164", 0.41, "d1", False)
