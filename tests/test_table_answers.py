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
- a table row the question names by a cell is found, as that viewer only.
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
