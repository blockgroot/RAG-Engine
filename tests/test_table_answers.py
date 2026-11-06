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
from dataclasses import replace

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
    return _checked(question, reply, table, spec)


def _checked(question: str, reply: str, table: TableRef, spec: ChartSpec) -> ChartSpec:
    """The two table checks, in ``classify_question``'s order."""
    handles = {"T1": table}
    intent = resolve._typed_values(AskIntent("chart", spec=spec), question, handles)
    return resolve._table_period_axis(intent, question, reply, handles).spec


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
    owner, region = _col(leads, "Owner"), _col(leads, "Region")
    one = _picked("Lead count by region for Rohan Mehta", '{"group_by": "Region"}', leads)
    assert (owner, "Rohan Mehta") in one.filters
    two = _checked("Kavya Nair vs Rohan Mehta by region", '{"group_by": "Region"}', leads,
                   ChartSpec(metric=TABLE_METRIC, group_by=region, period="month", chart="bar",
                             measure="count", table_id=leads.id))
    assert (owner, ("Kavya Nair", "Rohan Mehta")) in two.filters
    assert (two.group_by, two.split_by) == (region, owner)


def test_a_word_of_a_header_the_question_names_is_not_a_typed_value():
    """Staging, Claude Haiku: "leads per month by applied via" filtered Lead
    Status to "Applied": the word was the column's own name."""
    raw = extract.profile(extract.parse_csv(
        "Date,Lead Status,Applied Via\n2026-10-01,Applied,LinkedIn\n2026-10-02,Offer,Dice\n"
        "2026-11-03,Applied,Dice\n", name="Leads"))
    leads = TableRef(id=uuid.uuid4().hex, name="Leads",
                     columns=tuple(c.as_dict() for c in raw.columns), row_count=3,
                     truncated=False, notes=(), document_title="Leads", provider="google",
                     source_uri=None, document_id=uuid.uuid4().hex)
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(leads, "Date"), period="month",
                     chart="stacked_bar", measure="count", table_id=leads.id,
                     split_by=_col(leads, "Applied Via"))
    assert _checked("Leads per month by applied via", '{"period": "month"}', leads,
                    spec).filters == ()

    assert _checked("Applied leads per month", '{"period": "month"}', leads,
                    spec).filters == ((_col(leads, "Lead Status"), "Applied"),)


def _dated(name: str, csv: str) -> TableRef:
    raw = extract.profile(extract.parse_csv(csv, name=name))
    return TableRef(id=uuid.uuid4().hex, name=name,
                    columns=tuple(c.as_dict() for c in raw.columns), row_count=3,
                    truncated=False, notes=(), document_title=name, provider="google",
                    source_uri=None, document_id=uuid.uuid4().hex)


def test_a_named_month_on_a_table_is_a_filter_on_its_date_column():
    """Claude Haiku quoted "in November" as range_words and left filters
    empty, so every week of the sheet was drawn."""
    leads = _dated("Leads", "Date,Stage\n2026-10-01,New\n2026-11-03,Won\n2026-12-02,New\n")
    date_key = _col(leads, "Date")
    spec = ChartSpec(metric=TABLE_METRIC, group_by=date_key, period="week", chart="bar",
                     measure="count", table_id=leads.id)
    reply = '{"range_words": "in November", "range_period": "2026-11"}'
    out = resolve._table_range(AskIntent("chart", spec=spec), "Weekly leads in November",
                               reply, {"T1": leads})
    assert out.spec.filters == ((date_key, "2026-11"),)
    # Words the asker never wrote, or a value that is not a date, change nothing.
    for question, bad in (("Weekly leads", reply),
                          ("Weekly leads in November", '{"range_words": "in November", '
                                                       '"range_period": "November"}')):
        assert resolve._table_range(AskIntent("chart", spec=spec), question, bad,
                                    {"T1": leads}).spec.filters == ()


def test_a_named_month_outside_the_tables_dates_is_refused_naming_the_one_that_has_it():
    """The sales sheet ends in October: its November chart would be empty."""
    sales = _dated("Sales", "Created,Stage\n2026-06-01,New\n2026-10-27,Won\n")
    recruit = _dated("Recruit", "Date,Stage\n2026-10-01,New\n2026-12-31,Won\n")
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(sales, "Created"), period="week",
                     chart="bar", measure="count", table_id=sales.id)
    out = resolve._table_range(AskIntent("chart", spec=spec), "Weekly leads in November",
                               '{"range_words": "in November", "range_period": "2026-11"}',
                               {"T1": sales, "T2": recruit})
    assert out.kind == "refuse"
    assert "2026-06-01 to 2026-10-27" in out.message and '"Recruit"' in out.message


def test_a_value_beyond_the_columns_samples_is_a_filter_when_the_index_found_it():
    """Staging, Claude Haiku: "Daniel Reed's leads by lead status" charted every
    profile: a 25-name column lists twelve samples, and his was not one."""
    leads = _leads()
    owner = next(c for c in leads.columns if c["name"] == "Owner")
    columns = tuple({**c, "samples": c["samples"][:1], "distinct": 9}
                    if c is owner else c for c in leads.columns)
    leads = replace(leads, columns=columns, typed=(("Owner", "Rohan Mehta"),))
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(leads, "Stage"), period="month",
                     chart="bar", measure="count", table_id=leads.id)
    spec = resolve._typed_values(AskIntent("chart", spec=spec), "Rohan Mehta's leads by stage",
                                 {"T1": leads}).spec
    assert (_col(leads, "Owner"), "Rohan Mehta") in spec.filters


def test_a_comparison_the_model_narrowed_to_one_person_keeps_both():
    """Staging, Claude Haiku: "Compare Kavya Nair and Rohan Mehta won revenue by
    month" came back filtered on Kavya Nair alone."""
    leads = _leads()
    owner, created = _col(leads, "Owner"), _col(leads, "Created")
    spec = ChartSpec(metric=TABLE_METRIC, group_by=created, period="month", chart="bar",
                     measure="sum", value=_col(leads, "Deal Value"), table_id=leads.id,
                     filters=((owner, "Kavya Nair"),))
    spec = _checked("Compare Kavya Nair and Rohan Mehta won revenue by month",
                    '{"period": "month", "period_words": "by month"}', leads, spec)
    assert (owner, ("Kavya Nair", "Rohan Mehta")) in spec.filters
    assert (spec.group_by, spec.split_by) == (created, owner)


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
def test_another_orgs_sheet_is_never_found_or_offered(org, org_cleanup):
    """The cell index spans every org: the org filter in the query is the
    boundary. The same email in another org sees only that org's rows."""
    with get_connection() as conn:
        other = str(conn.execute("INSERT INTO organizations (name) VALUES (%s) RETURNING id",
                                 (f"rows-{uuid.uuid4().hex[:8]}",)).fetchone()[0])
        conn.commit()
    org_cleanup.append(other)
    mine = _sheet(org, ["sana@acme.test"])
    viewer = Viewer(email="sana@acme.test")

    def found(org_id):
        hits = table_store.rows_naming("Who owned L-1008?", org_id=org_id, workspace_id=None,
                                       viewer=viewer, providers=["google"])
        offered = table_store.list_tables(org_id=org_id, workspace_id=None, viewer=viewer)
        return {h.document_id for h in hits}, {t.document_id for t in offered}

    assert found(org) == ({mine}, {mine})
    assert found(other) == (set(), set())
    theirs = _sheet(other, ["sana@acme.test"])
    assert found(other) == ({theirs}, {theirs}) and found(org) == ({mine}, {mine})
    # Not shared on the sheet: neither its rows nor the table.
    assert table_store.list_tables(org_id=org, workspace_id=None,
                                   viewer=Viewer(email="someone@acme.test")) == []


@requires_db
def test_the_cells_a_question_types_are_found_per_table(org):
    """Staging, Claude Haiku: "Daniel Reed's leads by lead status" charted
    another leads sheet; only one held that name. The pick is told."""
    _sheet(org, ["sana@acme.test"])
    [table] = table_store.list_tables(org_id=org, workspace_id=None,
                                      viewer=Viewer(email="sana@acme.test"))
    typed = table_store.typed_cells("Kavya Nair's leads by stage", [table])
    assert typed == {table.id: (("Owner", "Kavya Nair"),)}
    # A header the question names is the column: "Region" is not a typed value.
    assert table_store.typed_cells("Leads by region", [table]) == {}
    note = doc_tables.catalogue([replace(table, typed=typed[table.id])])
    assert 'the question types its values: Owner "Kavya Nair"' in note


@requires_db
def test_a_question_naming_a_sheets_column_or_cell_reaches_the_form(org):
    from app.agent.routing import _names_a_sheet

    _sheet(org, ["sana@acme.test"])
    me = Viewer(email="sana@acme.test")
    assert _names_a_sheet("Which region has the most deals?", org, None, me)
    assert _names_a_sheet("How is Kavya Nair doing?", org, None, me)
    assert not _names_a_sheet("What is the leave policy?", org, None, me)
    # Not shared on the sheet: nothing names it.
    assert not _names_a_sheet("How is Kavya Nair doing?", org, None,
                              Viewer(email="someone@acme.test"))


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
def test_a_value_the_rows_do_not_hold_is_said_never_zero(org):
    """Dropped, the answer had nothing to say no with: a small model answered 0."""
    text = _worked(org, {"table": "T1", "operation": "count",
                         "filters": {"Owner": "Priya Sharma"}})
    assert "has no Owner 'Priya Sharma'" in text and "0" not in text.split(":", 1)[-1]


@requires_db
def test_a_value_under_the_wrong_column_is_read_from_the_one_that_holds_it(org):
    # "South" is a Region, not an Owner: the data's own words decide.
    text = _worked(org, {"table": "T1", "operation": "count",
                         "filters": {"Owner": "south"}})
    assert "Region is South" in text


@requires_db
def test_distinct_counts_different_values_and_lists_them(org):
    text = _worked(org, {"table": "T1", "operation": "distinct", "column": "Region",
                         "filters": {"Owner": "Kavya Nair"}, "label": "regions"})
    rows = [r for r in LEADS.splitlines()[1:] if "Kavya Nair" in r]
    assert f"over {len(rows)} rows" in text and "different Region values" in text


@requires_db
def test_a_period_filter_counts_its_dates_and_a_period_outside_them_is_said(org):
    inside = _worked(org, {"table": "T1", "operation": "count",
                           "filters": {"Created": "2026-06"}, "label": "june"})
    assert re.search(r"over \d+ rows", inside) and "Created from 2026-06-01 to 2026-06-30" in inside
    outside = _worked(org, {"table": "T1", "operation": "count",
                            "filters": {"Created": "2025-03"}, "label": "march"})
    assert "has no rows with Created in 2025-03: its Created runs from" in outside


@requires_db
def test_a_filter_on_a_word_of_the_sheets_own_headers_is_answered_both_ways(org):
    """Staging, Claude Haiku: "which company have we applied to the most?"
    filtered Lead Status to Applied and answered 18 where the sheet says 45."""
    with get_connection() as conn:
        doc = conn.execute(
            "INSERT INTO documents (org_id, title, source_uri, source_provider, "
            "source_external_id, doc_is_public, doc_viewers) VALUES "
            "(%s, 'Apps', 'https://drive.test/a', 'google', %s, false, %s) RETURNING id",
            (org, uuid.uuid4().hex, ["sana@acme.test"])).fetchone()
        conn.commit()
    raw = extract.parse_csv("Company,Lead Status,Applied Via\nAcme,Applied,Dice\n"
                            "Acme,Offer,Dice\nAcme,Rejected,LinkedIn\nBeta,Applied,Dice\n"
                            "Beta,Applied,LinkedIn\n", name="Apps")
    _store_tables(str(doc[0]), SourceDocument(external_id="a", title="Apps", content="",
                                              tables=[raw]), org_id=org, workspace_id=None)
    tables = table_store.list_tables(org_id=org, workspace_id=None,
                                     viewer=Viewer(email="sana@acme.test"))
    form = _Form([{"table": "T1", "operation": "count", "group_by": "Company",
                   "filters": {"Lead Status": "Applied"}, "label": "most applied"}])
    text = "\n".join(a.text for a in table_answers.answer(
        "Which company have we applied to the most?", tables, llm=form))
    assert "rows where Lead Status is Applied), over 3 rows: Beta: 2; Acme: 1" in text
    assert "most applied, every row" in text and "Acme: 3; Beta: 2" in text
    assert text.index("every row") < text.index("Of these rows, only Lead Status Applied")
    # "Offer" is the asker's own word and no header's: one reading.
    form = _Form([{"table": "T1", "operation": "count", "filters": {"Lead Status": "Offer"}}])
    assert "Of these" not in "".join(a.text for a in table_answers.answer(
        "How many offers?", tables, llm=form))
    # Stages the asker never typed are the model's reading of their word: both.
    form = _Form([{"table": "T1", "operation": "count", "label": "interviews",
                   "filters": {"Company": "Acme", "Lead Status": ["Offer", "Rejected"]}}])
    text = "".join(a.text for a in table_answers.answer(
        "How many interviews has Acme had?", tables, llm=form))
    assert "interviews, every row (from \"Apps\", rows where Company is Acme), over 3 rows" in text
    assert "Of these rows, only Lead Status Offer or Rejected: interviews" in text


@requires_db
def test_a_name_the_question_types_that_no_table_holds_is_said_and_no_figure_given(org):
    """Staging, Claude Haiku: "How many companies has Jordan Blake applied to?"
    left him out of the form, and the answer gave him every row's 420."""
    form = _Form([{"table": "T1", "operation": "count"}])
    form._reply = json.dumps({"named": ["Jordan Blake"],
                              "queries": [{"table": "T1", "operation": "count"}]})
    [only] = table_answers.answer("How many deals has Jordan Blake closed?",
                                  _sales_tables(org), llm=form)
    assert 'none holds "Jordan Blake"' in only.text and "over" not in only.text
    # A name quoted but not in the question is not the asker's: ignored.
    form._reply = json.dumps({"named": ["Jordan Blake"],
                              "queries": [{"table": "T1", "operation": "count"}]})
    assert "none holds" not in "".join(
        a.text for a in table_answers.answer("How many deals?", _sales_tables(org), llm=form))


@requires_db
def test_a_part_of_one_stored_value_filters_on_that_value(org):
    form = _Form([{"table": "T1", "operation": "count", "filters": {"Owner": "Kavya"},
                   "label": "kavya"}])
    form._reply = json.dumps({"named": ["Kavya"], "queries": json.loads(form._reply)["queries"]})
    text = "".join(a.text for a in table_answers.answer("How many leads has Kavya?",
                                                        _sales_tables(org), llm=form))
    assert "Owner is Kavya Nair" in text


@requires_db
def test_a_value_the_question_types_is_added_to_a_query_that_left_it_out(org):
    """Claude Haiku's form dropped typed names; the figure was every row's."""
    form = _Form([{"table": "T1", "operation": "count", "filters": {"Stage": "Won"},
                   "label": "won"}])
    text = "".join(a.text for a in table_answers.answer(
        "How many deals has Kavya Nair won?", _sales_tables(org), llm=form))
    assert "Stage is Won and Owner is Kavya Nair" in text
    # Already grouped by that column: nothing is added.
    form = _Form([{"table": "T1", "operation": "count", "group_by": "Owner", "label": "by"}])
    text = "".join(a.text for a in table_answers.answer(
        "Kavya Nair vs Rohan Mehta lead count", _sales_tables(org), llm=form))
    assert "- by (from \"Leads\"), over 5 rows" in text


def test_a_header_written_short_is_the_one_header_containing_it():
    """Claude Haiku wrote "Applied Via (O)" for "Also Applied Via (O)"; the query
    was refused and the question fell to a chart of something else."""
    raw = extract.profile(extract.parse_csv(
        "Applied Via,Also Applied Via (O),Lead Status\nDice,LinkedIn,Offer\n"
        "LinkedIn,Dice,Applied\n", name="Leads"))
    leads = TableRef(id="t", name="Leads", columns=tuple(c.as_dict() for c in raw.columns),
                     row_count=2, truncated=False, notes=(), document_title="Leads",
                     provider="google", source_uri=None, document_id="d")
    find = lambda name: doc_tables._find(leads, name, types=("category",), role="filter")  # noqa: E731
    assert find("Applied Via (O)") == _col(leads, "Also Applied Via (O)")
    assert find("Applied Via") == _col(leads, "Applied Via")  # exact still wins
    with pytest.raises(doc_tables.TableRefusal):
        find("Via")  # two headers hold it: neither


def test_the_one_column_a_question_names_is_the_breakdown():
    """Claude Haiku grouped "which recruiter handled the most offers?" by BDE."""
    raw = extract.profile(extract.parse_csv(
        "Recruit,BDE,Lead Status\nTanvi,Aman,Offer\nIshaan,Neha,Offer\n", name="Leads"))
    leads = TableRef(id="t", name="Leads", columns=tuple(c.as_dict() for c in raw.columns),
                     row_count=2, truncated=False, notes=(), document_title="Leads",
                     provider="google", source_uri=None, document_id="d")
    query = {"operation": "count", "group_by": "BDE", "filters": {"Lead Status": "Offer"}}
    assert table_answers._named_breakdown(
        leads, query, "Which recruiter handled the most offers?")["group_by"] == "Recruit"
    # It names no column, or the one already drawn: unchanged.
    assert table_answers._named_breakdown(leads, query, "Who closed the most?") == query
    assert table_answers._named_breakdown(leads, query, "Which BDE has the most?") == query



def test_a_value_in_two_columns_is_one_filter_on_the_column_the_question_names():
    """Staging, the synced workbook: "LinkedIn" is a cell of Applied Via and of
    Also Applied Via (O). Haiku's right filter got the other column added
    (0 rows); "via LinkedIn" put on the "Also" column counted 12, not 109."""
    t = TableRef(id="t", name="Leads", columns=(), row_count=0, truncated=False, notes=(),
                 document_title="Leads", provider="google", source_uri=None, document_id="d")
    typed = (("Applied Via", "LinkedIn"), ("Also Applied Via (O)", "LinkedIn"))
    q = {"operation": "count", "filters": {"Applied Via": "LinkedIn"}}
    assert table_answers._with_typed(t, q, "How many went through LinkedIn?", typed) == q
    moved = table_answers._with_typed(
        t, {"operation": "count", "filters": {"Also Applied Via (O)": "LinkedIn"}},
        "How many leads did Aman get via LinkedIn?", typed)
    assert moved["filters"] == {"Applied Via": "LinkedIn"}
    # Left out entirely: added to the named column only, and to none on a tie.
    assert table_answers._with_typed(t, {"operation": "count", "filters": {}},
                                     "Leads via LinkedIn", typed)["filters"] == {
        "Applied Via": "LinkedIn"}
    assert table_answers._with_typed(t, {"operation": "count", "filters": {}},
                                     "LinkedIn leads", typed)["filters"] == {}
    # One value in each column, as the model wrote it: never moved onto the other.
    typed2 = typed + (("Applied Via", "Dice"), ("Also Applied Via (O)", "Dice"))
    both = {"operation": "count", "filters": {"Applied Via": "LinkedIn",
                                              "Also Applied Via (O)": "Dice"}}
    assert table_answers._with_typed(
        t, both, "How many were made via LinkedIn and also via Dice?", typed2) == both

@requires_db
def test_of_these_outside_a_ratio_narrows_the_rows_never_silently_ignored(org):
    text = _worked(org, {"table": "T1", "operation": "count", "filters": {"Owner": "Kavya Nair"},
                         "of_these": {"Stage": "Won"}, "label": "kavya won"})
    assert "rows where Owner is Kavya Nair and Stage is Won), over 2 rows: 2" in text


@requires_db
def test_rows_lists_the_matching_records_with_their_columns(org):
    text = _worked(org, {"table": "T1", "operation": "rows",
                         "filters": {"Owner": "Kavya Nair", "Stage": "Won"}, "label": "won"})
    assert "Owner: Kavya Nair" in text and "Stage: Won" in text


@requires_db
def test_a_stand_in_the_model_marks_as_not_the_figure_is_dropped(org):
    assert _worked(org, {"table": "T1", "operation": "count", "group_by": "Region",
                         "is_the_figure_asked": False}) == ""


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
def test_a_chart_filtered_to_a_month_counts_only_its_days_and_keeps_the_date_axis(org):
    """Staging, Claude Haiku: "leads by lead type in December" was refused --
    a date column could not be a filter -- and "weekly leads in November"
    drew every week."""
    from app.agent.insights_agent import run_table_spec

    [leads] = _sales_tables(org)
    created, stage = _col(leads, "Created"), _col(leads, "Stage")
    pick = doc_tables.parse_pick({"table": "T1", "group_by": "Stage",
                                  "filters": {"Created": "2026-06"}}, {"T1": leads})
    assert pick.filters == ((created, "2026-06"),)
    spec = ChartSpec(metric=TABLE_METRIC, group_by=stage, period="month", chart="bar",
                     measure="count", table_id=leads.id, filters=pick.filters)
    assert resolve.spec_from_dict(resolve.spec_to_dict(spec)) == spec
    panel, _ = run_table_spec(spec, org_id=org, workspace_id=None,
                              viewer=Viewer(email="sana@acme.test"))
    june = [r for r in LEADS.splitlines()[1:] if r.startswith("2026-06")]
    assert sum(p["value"] for p in panel["points"]) == len(june)
    assert "2026-06-01 to 2026-06-30" in panel["title"]
    # Filtered to a month, the date column is still the axis it was.
    weekly = resolve._table_period_axis(
        AskIntent("chart", spec=replace(spec, group_by=created, period="week")),
        "Weekly leads in June", '{"period": "week", "period_words": "weekly"}', {"T1": leads})
    assert weekly.spec.group_by == created
    with pytest.raises(doc_tables.TableRefusal):
        doc_tables.parse_pick({"table": "T1", "filters": {"Created": "December"}}, {"T1": leads})


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


def test_a_named_row_scores_the_better_of_its_document_and_its_own_text(monkeypatch):
    """Staging: "Who closed L-1076?" found the row, but the sheet's column
    description scored 0.342 and the gate refused before the model saw it."""
    row = table_store.RowHit(document_id="d1", document_title="Leads", provider="google",
                             source_uri=None, table_name="Leads", row_no=75,
                             text="Lead ID: L-1076; Owner: Kavya Nair", is_public=False)

    class _Sheet:
        def query(self, *a, **k):
            return [RetrievedChunk(content="cols", score=0.342, document_id="d1",
                                   chunk_index=0, org_id="org", doc_is_public=False)]

    class _Embedder:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("app.doctables.store.rows_naming", lambda *a, **k: [row])
    monkeypatch.setattr("app.agent.routing._probe_embedder", lambda: _Embedder())
    retriever = HybridRetriever(store=_Sheet(), reranker=None)
    [hit] = retriever._table_rows("org", "Who closed L-1076?", [0.6, 0.8], workspace_id=None,
                                  viewer=Viewer(email="x@acme.test"), tools=["google"])
    assert hit.score == pytest.approx(0.6)  # the row's own cosine, above its sheet's 0.342


def test_a_copy_of_a_sheet_is_offered_once_but_a_template_s_months_stay():
    """Staging: "Sales Targets 2026" and "Sales Targets 2026.csv" took two of
    the six chart slots and pushed the leads sheet out."""
    from dataclasses import replace as _replace

    leads = _leads()
    copy = _replace(leads, id="t2", document_title="Leads.csv", name="Leads.csv")
    june = _replace(leads, id="t3", document_title="Leads June", name="Leads June")
    assert [t.id for t in doc_tables.distinct([leads, copy, june])] == [leads.id, "t3"]


def test_a_date_breakdown_stays_the_axis_wherever_its_column_sits():
    """Staging, Sonnet: "revenue by region by month" picked Closed x Region; Region
    sits before Closed, so it was swapped to Region x Closed and refused."""
    table = extract.profile(extract.parse_csv(
        "Region,Closed,Value\nSouth,2026-06-01,5\nWest,2026-07-01,6\n", name="Deals"))
    ref = TableRef(id="t", name="Deals", columns=tuple(c.as_dict() for c in table.columns),
                   row_count=2, truncated=False, notes=(), document_title="Deals",
                   provider="google", source_uri=None, document_id="d")
    pick = doc_tables.parse_pick({"table": "T1", "value": "Value", "group_by": "Closed",
                                  "split_by": "Region"}, {"T1": ref})
    assert (pick.group_by, pick.split_by) == (_col(ref, "Closed"), _col(ref, "Region"))


@requires_db
def test_a_table_stored_with_five_samples_is_resampled_from_its_rows(org):
    """Tables indexed before 2026-11 kept five values per category column, and
    "Won" was not among a Stage column's. The one-time fix in schema.sql
    re-reads them from the stored rows; this runs that exact statement."""
    from pathlib import Path

    sql = Path(__file__).resolve().parents[1].joinpath("app/db/schema.sql").read_text()
    statement = sql.split("-- category_samples_twelve:begin")[1].split(
        "-- category_samples_twelve:end")[0]
    _sheet(org, ["sana@acme.test"])
    with get_connection() as conn:
        (table_id, columns), = conn.execute(
            "SELECT id, columns FROM doc_tables WHERE org_id = %s", (org,)).fetchall()
        stage = next(c for c in columns if c["name"] == "Stage")
        assert stage["samples"] == ["Lost", "New", "Won"]
        stage["samples"] = ["Lost", "New"]  # what an old ingest kept
        conn.execute("UPDATE doc_tables SET columns = %s::jsonb WHERE id = %s",
                     (json.dumps(columns), table_id))
        conn.execute(statement)
        (after,), = conn.execute("SELECT columns FROM doc_tables WHERE id = %s",
                                 (table_id,)).fetchall()
        conn.commit()
    assert next(c for c in after if c["name"] == "Stage")["samples"] == ["Lost", "New", "Won"]
    # Every other column is as it was, in order.
    assert [c["key"] for c in after] == [c["key"] for c in columns]


@requires_db
def test_a_date_column_stored_without_its_span_gets_it_from_its_rows(org):
    from pathlib import Path

    sql = Path(__file__).resolve().parents[1].joinpath("app/db/schema.sql").read_text()
    statement = sql.split("-- date_column_span:begin")[1].split("-- date_column_span:end")[0]
    _sheet(org, ["sana@acme.test"])
    with get_connection() as conn:
        (table_id, columns), = conn.execute(
            "SELECT id, columns FROM doc_tables WHERE org_id = %s", (org,)).fetchall()
        created = next(c for c in columns if c["name"] == "Created")
        span = (created.pop("first"), created.pop("last"))  # what an old ingest kept
        conn.execute("UPDATE doc_tables SET columns = %s::jsonb WHERE id = %s",
                     (json.dumps(columns), table_id))
        conn.execute(statement)
        (after,), = conn.execute("SELECT columns FROM doc_tables WHERE id = %s",
                                 (table_id,)).fetchall()
        conn.commit()
    fixed = next(c for c in after if c["name"] == "Created")
    assert (fixed["first"], fixed["last"]) == span
    assert [c["key"] for c in after] == [c["key"] for c in columns]


def test_a_named_row_reaches_the_reranker_when_sub_questions_crowd_fusion(monkeypatch):
    """Staging, Sonnet: "who does Rohan Mehta report to, and what is their
    email?" was split in two; every sheet description came back from each
    sub-question's vector and keyword search, the row from one leg, and fusion
    pushed the row out of the rerank pool. The reranker never saw it."""
    from dataclasses import replace

    from app.config.settings import RagSettings, RetrievalSettings
    from app.rag.retrieval import _ROW_INDEX

    def chunk(doc, index, score=0.5):
        return RetrievedChunk(content=f"{doc}:{index}", score=score, document_id=doc,
                              chunk_index=index, org_id="org", doc_is_public=False)

    docs = [chunk(f"d{i}", 0) for i in range(6)]
    row = chunk("sheet", _ROW_INDEX + 1, 0.66)
    lists = [docs, docs, docs, docs, [row]]

    class _Reranker:
        def rerank(self, query, candidates, keep):
            seen = sorted(candidates, key=lambda c: c.chunk_index < _ROW_INDEX)
            return [replace(c, score=1.0 - i * 0.01) for i, c in enumerate(seen)][:keep]

    settings = replace(RetrievalSettings.from_env(), candidate_pool=4, rerank_enabled=True)
    retriever = HybridRetriever(store=_NoHits(), reranker=_Reranker(), settings=settings,
                                rag_settings=RagSettings.from_env())
    monkeypatch.setattr(retriever, "_first_stage_all", lambda *a, **k: lists)
    monkeypatch.setattr(retriever, "_graph_documents", lambda *a, **k: ([], False))
    monkeypatch.setattr(retriever, "_named_tools", lambda *a, **k: [])
    hits = retriever.retrieve("org", "q?", [0.0], viewer=Viewer(email="x@acme.test")).hits
    assert hits[0].chunk_index == row.chunk_index


@pytest.mark.parametrize("question, reply, refused", [
    # "by region" quoted, the table has no such column: only a month axis is left.
    ("Revenue target by region by month",
     '{"period": "month", "period_words": "by month", "breakdown_words": "by region"}', True),
    # The breakdown quote IS the period: the date axis drew it.
    ("Revenue target per month", '{"period": "month", "period_words": "per month", '
     '"breakdown_words": "per month"}', False),
])
def test_an_asked_breakdown_the_chart_cannot_draw_is_refused(question, reply, refused):
    targets = _targets()
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(targets, "Month"), period="month",
                     chart="line", measure="sum", value=_col(targets, "Revenue Target"),
                     table_id=targets.id)
    intent = resolve._table_period_axis(AskIntent("chart", spec=spec), question, reply,
                                        {"T1": targets})
    assert (intent.kind == "refuse") is refused


def test_a_breakdown_dropped_on_a_read_pages_period_is_refused():
    """Staging, Claude Haiku: "revenue by region by month" drew the review
    page's monthly Revenue (a text Period column, no date) and left out region."""
    raw = extract.profile(extract.parse_csv(
        "Period,Revenue\nJuly,3226000\nAugust,3744000\n", name="Revenue"))
    page = TableRef(id=uuid.uuid4().hex, name="Revenue",
                    columns=tuple(c.as_dict() for c in raw.columns), row_count=2,
                    truncated=False, notes=(), document_title="Q3 review", provider="google",
                    source_uri=None, origin="text", document_id=uuid.uuid4().hex)
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(page, "Period"), period="month",
                     chart="bar", measure="sum", value=_col(page, "Revenue"), table_id=page.id)
    reply = '{"period": "month", "period_words": "by month", "breakdown_words": "by region"}'
    intent = resolve._table_period_axis(AskIntent("chart", spec=spec),
                                        "Revenue by region by month", reply, {"T1": page})
    assert intent.kind == "refuse"
    # Only the period asked: drawn.
    reply = '{"period": "month", "period_words": "monthly", "breakdown_words": "monthly"}'
    intent = resolve._table_period_axis(AskIntent("chart", spec=spec),
                                        "Monthly revenue from the review", reply, {"T1": page})
    assert intent.kind == "chart"


def _targets_sheet(org_id: str, viewers: list[str]) -> str:
    with get_connection() as conn:
        doc = conn.execute(
            """
            INSERT INTO documents (org_id, title, source_uri, source_provider,
                                   source_external_id, doc_is_public, doc_viewers)
            VALUES (%s, 'Targets', 'https://drive.test/t', 'google', %s, false, %s)
            RETURNING id
            """,
            (org_id, uuid.uuid4().hex, viewers),
        ).fetchone()
        conn.commit()
    raw = extract.parse_csv("Employee,Target\nKavya Nair,1000000\nRohan Mehta,900000\n"
                            "Arjun Iyer,500000\n", name="Targets")
    _store_tables(str(doc[0]), SourceDocument(external_id="t", title="Targets", content="",
                                              tables=[raw]), org_id=org_id, workspace_id=None)
    return str(doc[0])


@requires_db
def test_two_sheets_are_compared_per_name_and_need_both_open(org):
    """"Who missed their revenue target?": targets in one sheet, won deals in
    another. Joined on the names both hold; a name only one side has is said."""
    _sheet(org, ["sana@acme.test"])
    targets_doc = _targets_sheet(org, ["sana@acme.test"])
    tables = table_store.list_tables(org_id=org, workspace_id=None,
                                     viewer=Viewer(email="sana@acme.test"))
    handles = {t.name: f"T{i}" for i, t in enumerate(tables, start=1)}
    [passage] = table_answers.answer("who missed target?", tables, llm=_Form([{
        "table": handles["Leads"], "operation": "compare", "value": "Deal Value",
        "group_by": "Owner", "filters": {"Stage": "Won"}, "label": "won vs target",
        "against": {"table": handles["Targets"], "operation": "sum", "value": "Target",
                    "group_by": "Employee"}}]))
    # Kavya: 980,000 + 1,206,000 won against 1,000,000. Rohan and Arjun own
    # leads but won none: 0 against their targets, not "missing".
    assert "Kavya Nair: 2,186,000 vs 1,000,000 (the first is above the second" in passage.text
    assert "Rohan Mehta: 0 vs 900,000 (the first is below the second" in passage.text
    assert "Only in" not in passage.text
    assert passage.requires == (targets_doc,)


def test_a_comparison_is_dropped_when_the_second_sheet_cannot_be_opened(monkeypatch):
    figure = table_answers.TableAnswer(provider="google", document_id="d1",
                                       document_title="Leads", source_uri=None,
                                       text="won vs target", requires=("d2",))

    class _OnlyFirst:
        def query(self, *a, **k):
            return [RetrievedChunk(content="cols", score=0.5, document_id="d1",
                                   chunk_index=0, org_id="org", doc_is_public=False)]

    monkeypatch.setattr("app.doctables.store.rows_naming", lambda *a, **k: [])
    retriever = HybridRetriever(store=_OnlyFirst(), reranker=None)
    assert retriever._table_rows("org", "q?", [0.0], workspace_id=None,
                                 viewer=Viewer(email="x@acme.test"), tools=["google"],
                                 worked=(figure,)) == []


@requires_db
def test_a_target_vs_achieved_chart_draws_both_tables_per_name(org):
    """"Revenue target vs achieved by employee": targets in one sheet, won
    deals in another, one pair of bars per person."""
    from app.agent.insights_agent import run_table_spec
    from app.insights.resolve import Versus

    _sheet(org, ["sana@acme.test"])
    _targets_sheet(org, ["sana@acme.test", "other@acme.test"])
    viewer = Viewer(email="sana@acme.test")
    tables = {t.name: t for t in table_store.list_tables(org_id=org, workspace_id=None,
                                                         viewer=viewer)}
    targets, leads = tables["Targets"], tables["Leads"]
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(targets, "Employee"), period="month",
                     chart="bar", measure="sum", value=_col(targets, "Target"),
                     table_id=targets.id,
                     versus=Versus(table_id=leads.id, group_by=_col(leads, "Owner"),
                                   measure="sum", value=_col(leads, "Deal Value"),
                                   filters=((_col(leads, "Stage"), "Won"),)))
    assert resolve.spec_from_dict(resolve.spec_to_dict(spec)) == spec
    panel, _ = run_table_spec(spec, org_id=org, workspace_id=None, viewer=viewer)
    bars = {(p["group"], p["series"]): p["value"] for p in panel["points"]}
    assert bars[("Kavya Nair", "Target")] == 1000000.0
    assert bars[("Kavya Nair", "Deal Value")] == 2186000.0
    assert bars[("Rohan Mehta", "Deal Value")] == 0.0   # owns leads, won none
    assert panel["title"].startswith("Target vs Deal Value by Employee")
    # Someone who can open the targets sheet but not the leads sheet gets no chart.
    from app.insights.resolve import CannotChart

    with pytest.raises(CannotChart, match="no longer available"):
        run_table_spec(spec, org_id=org, workspace_id=None, viewer=Viewer(email="other@acme.test"))


def test_a_versus_pick_is_validated_like_any_pick():
    leads, targets = _leads(), _targets()
    handles = {"T1": targets, "T2": leads}
    reply = json.dumps({"intent": "chart", "table": "T1", "value": "Revenue Target",
                        "group_by": "Employee", "versus": {
                            "table": "T2", "value": "Deal Value", "group_by": "Owner",
                            "filters": {"Stage": "Won"}}})
    intent = resolve._parse_intent(reply, [], fail_open=False, handles=handles, fields=None,
                                   missing=[], providers=["google"])
    assert intent.kind == "chart" and intent.spec.versus.table_id == leads.id
    assert intent.spec.versus.filters == ((_col(leads, "Stage"), "Won"),)
    # Grouped by its date, a sheet with ONE names column is matched on that column.
    by_month = json.dumps({"intent": "chart", "table": "T1", "value": "Revenue Target",
                           "group_by": "Month", "versus": {"table": "T2", "value": "Deal Value",
                                                            "group_by": "Owner"}})
    intent = resolve._parse_intent(by_month, [], fail_open=False, handles=handles, fields=None,
                                   missing=[], providers=["google"])
    assert intent.spec.group_by == _col(targets, "Employee")
    # A sheet with several such columns grouped by a date is refused, never guessed.
    bad = json.dumps({"intent": "chart", "table": "T2", "value": "Deal Value",
                      "group_by": "Created", "versus": {"table": "T1", "value": "Revenue Target",
                                                         "group_by": "Employee"}})
    assert resolve._parse_intent(bad, [], fail_open=False, handles=handles, fields=None,
                                 missing=[], providers=["google"]).kind == "refuse"


def test_a_lookup_phrase_is_every_whole_word_stretch_of_the_question():
    """The index lookup must find exactly what a scan for each cell found:
    a cell standing as whole words, never part of a word."""
    from app.doctables.store import _phrases

    phrases = set(_phrases("Who closed L-1008 for Rohan Mehta's team?"))
    assert {"l-1008", "rohan mehta", "rohan mehta's team", "who closed l-1008"} <= phrases
    assert "ohan" not in phrases and "l-10" not in phrases and "mehta's tea" not in phrases


def test_a_counted_answer_over_several_periods_states_the_total():
    from types import SimpleNamespace

    from app.api.chat import _counted_answer

    chart = {"measure": "count", "unit": "", "points": [
        {"bucket": "2026-10-01", "group": None, "series": None, "value": 102.0},
        {"bucket": "2026-11-01", "group": None, "series": None, "value": 191.0},
        {"bucket": "2026-12-01", "group": None, "series": None, "value": 79.0}]}
    text = _counted_answer(SimpleNamespace(answer="Rows by month", chart=chart,
                                           chart_period="month"))
    assert text.endswith("- Total: 372")
    chart["measure"] = "average"
    assert "Total" not in _counted_answer(SimpleNamespace(answer="x", chart=chart,
                                                          chart_period="month"))


def test_a_chart_filters_a_value_in_two_columns_on_the_one_the_question_names():
    """"LinkedIn" is a value of Applied Via and of Also Applied Via (O): a chart
    must not filter both (no row has it in both) nor the "Also" one for "via"."""
    leads = _dated("Leads", "Date,Role,Applied Via,Also Applied Via (O)\n"
                   "2026-10-01,QA,LinkedIn,Dice\n2026-10-02,Dev,Dice,LinkedIn\n")
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(leads, "Role"), period="month",
                     chart="bar", measure="count", table_id=leads.id)
    handles = {"T1": leads}
    via = resolve._typed_values(AskIntent("chart", spec=spec), "Leads by role via LinkedIn",
                                handles).spec
    assert via.filters == ((_col(leads, "Applied Via"), "LinkedIn"),)
    tie = resolve._typed_values(AskIntent("chart", spec=spec), "LinkedIn leads by role",
                                handles).spec
    assert tie.filters == ()


def test_a_header_inside_the_breakdowns_header_is_not_a_second_breakdown():
    """Staging: "Daniel Reed's leads by lead status" with group_by Lead Status
    from Claude Haiku drew Lead Status x Status."""
    t = _dated("L", "Date,Profile Name,Lead Status,Status\n2026-10-01,Daniel Reed,Applied,Open\n"
               "2026-10-02,Daniel Reed,Offer,Closed\n")
    spec = ChartSpec(metric=TABLE_METRIC, group_by=_col(t, "Lead Status"), period="month",
                     chart="bar", measure="count", table_id=t.id,
                     filters=((_col(t, "Profile Name"), "Daniel Reed"),))
    out = resolve._table_period_axis(AskIntent("chart", spec=spec),
                                     "Daniel Reed's leads by lead status",
                                     '{"group_by": "Lead Status"}', {"T1": t}).spec
    assert (out.group_by, out.split_by) == (_col(t, "Lead Status"), None)


def test_a_month_in_named_is_a_period_not_a_name_no_cell_holds():
    """Staging: Claude Haiku named "December"; no cell holds the word, so the
    right figure (21) was dropped as about someone the sheet does not hold."""
    reply = '{"named": ["Sophia Turner", "December", "Dec 2026"], "queries": []}'
    question = "How many Dice applications did Sophia Turner make in December? Dec 2026"
    assert table_answers._named(reply, question) == ["Sophia Turner"]
