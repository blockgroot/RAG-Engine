"""A wide, messy ledger charted from every place a file can actually live.

The connector suite counts activity (who edited what). This one counts what a
file SAYS. One ledger of 360 rows — Indian grouping, lakh, crore, refunds in
parentheses, blanks, N/A, day-first dates, a percent column, and notes — is
written four ways and charted through the real agent:

- an uploaded CSV, Excel workbook (two sheets), and a ruled PDF
- a Google Sheet (first tab only, the way Drive exports one)
- a Notion page whose table is pipe text

The expected totals are the numbers chosen before they were formatted, not a
second call to the parser. A Drive PDF or a Drive .xlsx is not a chart source:
Drive keeps a Sheet's first tab and a Doc's pipe tables, and a PDF or workbook
is charted when someone uploads it.
"""

from __future__ import annotations

import io
import uuid
from collections import defaultdict
from datetime import date

import pytest

from app.agent.insights_agent import InsightsAgent
from app.attachments import store as attach_store
from app.db import get_connection
from app.doctables import store as table_store
from app.doctables.store import UploadScope
from app.doctables.uploads import read_upload_tables
from app.ingestion.pipeline import _store_tables
from app.insights.resolve import TABLE_METRIC, ChartSpec, classify_question
from app.sources.base import SourceDocument
from app.sources.google_drive import _table_document
from app.vectorstore.base import Viewer
from .conftest import requires_db
from .test_attachment_storage import FakeBlobStore
from .test_doctables_uploads import ruled_pdf

pytestmark = requires_db

SANA = Viewer(email="sana@acme.test")
DEPTS = ("Engineering", "Design", "Sales", "Finance", "People", "Legal")
REGIONS = ("North", "South", "West", "East")
N = 360


def _indian(n: int) -> str:
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = [tail]
    while head:
        parts.append(head[-2:])
        head = head[:-2]
    return ",".join(reversed(parts))


def _ledger():
    """360 rows, and the totals those rows mean before any formatting."""
    rows = []
    by_dept, by_region, by_month, north = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float)
    skipped = 0
    for i in range(N):
        dept, region = DEPTS[i % 6], REGIONS[i % 4]
        when = date(2026, (i % 6) + 1, 15)
        if i % 17 == 0:
            kind, amount = "na", None
            skipped += 1
        elif i % 19 == 0:
            kind, amount = "blank", None
        elif i == 1:
            kind, amount = "crore", 2 * 10_000_000
        elif i % 11 == 0:
            kind, amount = "refund", -((i + 1) * 250)
        elif i % 7 == 0:
            kind, amount = "lakh", (i % 5 + 1) * 100_000
        else:
            kind, amount = "inr", (i + 1) * 1000
        margin = (i % 20) + 0.5
        rows.append({
            "dept": dept, "region": region, "when": when, "amount": amount,
            "kind": kind, "margin": margin, "note": f"invoice {i} for {dept}",
        })
        if amount is None:
            continue
        by_dept[dept] += amount
        by_region[region] += amount
        by_month[when.strftime("%Y-%m")] += amount
        if region == "North":
            north[dept] += amount
    return rows, dict(by_dept), dict(by_region), dict(by_month), dict(north), skipped


ROWS, BY_DEPT, BY_REGION, BY_MONTH, NORTH, SKIPPED = _ledger()


def _amount_text(row) -> str:
    amount, kind = row["amount"], row["kind"]
    if kind == "na":
        return "N/A"
    if kind == "blank":
        return ""
    if kind == "crore":
        return "₹2Cr"
    if kind == "lakh":
        return f"₹{int(amount // 100_000)}L"
    if kind == "refund":
        return f"(₹{_indian(int(abs(amount)))})"
    return f"₹{_indian(int(amount))}"


def _csv_text() -> str:
    lines = ["Department,Region,Month,Amount,Margin,Notes"]
    for row in ROWS:
        lines.append(
            f"{row['dept']},{row['region']},{row['when'].strftime('%d/%m/%Y')},"
            f"\"{_amount_text(row)}\",{row['margin']}%,{row['note']}"
        )
    return "\n".join(lines) + "\n"


def _xlsx() -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    spend = book.active
    spend.title = "Spend"
    spend.append(["Department", "Region", "Month", "Amount", "Margin", "Notes"])
    for row in ROWS:
        spend.append([
            row["dept"], row["region"], row["when"].strftime("%d/%m/%Y"),
            _amount_text(row), f"{row['margin']}%", row["note"],
        ])
    people = book.create_sheet("Headcount")
    people.append(["Department", "People"])
    for i, dept in enumerate(DEPTS, start=1):
        people.append([dept, str(i * 3)])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _pdf_rows():
    """One page of the same ledger. The rupee sign is not in the built-in PDF
    font, so the page writes Rs., which is the same amount. Parentheses are
    escaped because they delimit a PDF string."""
    def cell(text):
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    out = [["Department", "Region", "Amount"]]
    for row in ROWS[:24]:
        shown = (_amount_text(row) or "N/A").replace("₹", "Rs.")
        out.append([cell(row["dept"]), cell(row["region"]), cell(shown)])
    return out


def _notion_markdown() -> str:
    lines = [
        "# Quarterly spend ledger",
        "",
        "| Department | Region | Month | Amount | Margin | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in ROWS:
        lines.append(
            f"| {row['dept']} | {row['region']} | {row['when'].strftime('%d/%m/%Y')} "
            f"| {_amount_text(row)} | {row['margin']}% | {row['note']} |"
        )
    return "\n".join(lines)


def _round(totals: dict) -> dict:
    return {k: round(v, 2) for k, v in totals.items()}


def _groups(response) -> dict:
    assert response.chart is not None, response.answer
    out = defaultdict(float)
    for point in response.chart["points"]:
        out[point["group"]] += point["value"]
    return _round(out)


def _months(response) -> dict:
    assert response.chart is not None, response.answer
    out = defaultdict(float)
    for point in response.chart["points"]:
        out[point["bucket"][:7]] += point["value"]
    return _round(out)


def _key(table, name):
    return next(c["key"] for c in table.columns if c["name"] == name)


def _spec(table, *, group, value="Amount", chart="bar", filters=()):
    return ChartSpec(
        metric=TABLE_METRIC, group_by=_key(table, group), period="month", chart=chart,
        measure="sum", value=_key(table, value), table_id=table.id, filters=filters,
    )


class _SaysPie:
    model = "test"
    last_usage = None

    def generate(self, prompt, *, max_tokens=None):
        return '{"intent": "qa", "metric": null, "chart": "pie"}'


@pytest.fixture
def wide(monkeypatch):
    monkeypatch.setattr(attach_store, "blobstore", FakeBlobStore())
    with get_connection() as conn:
        org = str(conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"wide-{uuid.uuid4().hex[:8]}",),
        ).fetchone()[0])
        user = str(conn.execute(
            "INSERT INTO users (email, org_id, role) VALUES (%s, %s, 'member') RETURNING id",
            (f"{uuid.uuid4().hex[:8]}@acme.test", org),
        ).fetchone()[0])
        conv = str(conn.execute(
            "INSERT INTO conversations (org_id, user_id) VALUES (%s, %s) RETURNING id",
            (org, user),
        ).fetchone()[0])
        conn.commit()
    saved = {}
    for filename, data in (
        ("ledger.csv", _csv_text().encode()),
        ("ledger.xlsx", _xlsx()),
        ("ledger.pdf", ruled_pdf(_pdf_rows())),
    ):
        row = attach_store.save_attachment(
            org_id=org, conversation_id=conv, user_id=user, filename=filename,
            content_type="application/octet-stream", content=filename,
            truncated=False, data=data,
        )
        read_upload_tables(
            row.id, org_id=org, user_id=user, filename=filename, data=data,
            text="", max_pdf_pages=5,
        )
        saved[filename] = row.id

    def document(title, provider, content, tables=None):
        with get_connection() as conn:
            doc_id = str(conn.execute(
                "INSERT INTO documents (org_id, title, source_uri, source_provider, "
                "source_external_id, doc_is_public, doc_viewers) "
                "VALUES (%s, %s, %s, %s, %s, true, %s) RETURNING id",
                (org, title, f"https://{provider}.test/{uuid.uuid4().hex}", provider,
                 uuid.uuid4().hex, []),
            ).fetchone()[0])
            conn.commit()
        _store_tables(
            doc_id,
            SourceDocument(external_id=doc_id, title=title, content=content, tables=tables),
            org_id=org, workspace_id=None,
        )
        return doc_id

    sheet_text, sheet_tables = _table_document(_csv_text(), name="Q1 spend ledger", sheet=True)
    document("Q1 spend ledger", "google", sheet_text, sheet_tables)
    document("Quarterly spend ledger", "notion", _notion_markdown())

    scope = UploadScope(conversation_id=conv, user_id=user)
    uploads = {t.document_title: t for t in table_store.list_upload_tables(
        org_id=org, uploads=scope)}
    # A workbook is several tables under one filename.
    books = [t for t in table_store.list_upload_tables(org_id=org, uploads=scope)
             if t.document_title == "ledger.xlsx"]
    pages = {t.document_title: t for t in table_store.list_tables(
        org_id=org, workspace_id=None, viewer=SANA)}
    held = {
        "org": org, "user": user, "conv": conv, "scope": scope,
        "csv": uploads["ledger.csv"], "pdf": uploads["ledger.pdf"],
        "sheets": {t.name: t for t in books}, "pages": pages,
    }
    yield held
    with get_connection() as conn:
        conn.execute("DELETE FROM organizations WHERE id = %s", (org,))
        conn.commit()


def test_the_uploaded_csv_is_a_pie_of_the_real_totals(wide):
    question = "From ledger.csv, show the total amount for each department as a pie."
    intent = classify_question(
        question, providers=["notion"], llm=_SaysPie(), fail_open=False,
        tables=[wide["csv"]],
    )
    assert intent.kind == "chart" and intent.spec.chart == "pie"
    response = InsightsAgent().answer(
        question, wide["org"], conversation_id=wide["conv"], user_id=wide["user"],
        viewer=SANA, spec=intent.spec,
    )
    assert response.chart["chart"] == "pie"
    assert _groups(response) == _round(BY_DEPT)
    assert f"{SKIPPED} Amount cells were not numbers" in response.chart["caveat"]
    assert wide["csv"].row_count == N and wide["csv"].truncated is False


def test_the_workbook_keeps_its_sheets_apart(wide):
    spend = wide["sheets"]["ledger.xlsx · Spend"]
    people = wide["sheets"]["ledger.xlsx · Headcount"]
    response = InsightsAgent().answer(
        "spend", wide["org"], conversation_id=wide["conv"], user_id=wide["user"],
        viewer=SANA, spec=_spec(spend, group="Department", chart="bar"),
    )
    assert _groups(response) == _round(BY_DEPT)
    headcount = InsightsAgent().answer(
        "people", wide["org"], conversation_id=wide["conv"], user_id=wide["user"],
        viewer=SANA, spec=_spec(people, group="Department", value="People"),
    )
    assert _groups(headcount) == {dept: float(i * 3) for i, dept in enumerate(DEPTS, start=1)}


def test_a_region_filter_and_a_month_line_use_the_same_rows(wide):
    table = wide["csv"]
    north = InsightsAgent().answer(
        "north", wide["org"], conversation_id=wide["conv"], user_id=wide["user"],
        viewer=SANA, spec=_spec(
            table, group="Department",
            filters=((_key(table, "Region"), "North"),),
        ),
    )
    assert _groups(north) == _round(NORTH)
    months = InsightsAgent().answer(
        "months", wide["org"], conversation_id=wide["conv"], user_id=wide["user"],
        viewer=SANA, spec=_spec(table, group="Month", chart="line"),
    )
    assert _months(months) == _round(BY_MONTH)
    # The percent column is a different measure. It must not be the rupee total.
    margin = InsightsAgent().answer(
        "margin", wide["org"], conversation_id=wide["conv"], user_id=wide["user"],
        viewer=SANA, spec=_spec(table, group="Department", value="Margin"),
    )
    assert sum(_groups(margin).values()) != pytest.approx(sum(BY_DEPT.values()))


def test_the_pdf_page_sums_the_rows_it_drew(wide):
    drawn = defaultdict(float)
    for row in ROWS[:24]:
        if row["amount"] is not None:
            drawn[row["region"]] += row["amount"]
    response = InsightsAgent().answer(
        "pdf", wide["org"], conversation_id=wide["conv"], user_id=wide["user"],
        viewer=SANA, spec=_spec(wide["pdf"], group="Region"),
    )
    assert _groups(response) == _round(drawn)


def test_a_google_sheet_and_a_notion_page_match_the_ledger(wide):
    sheet = wide["pages"]["Q1 spend ledger"]
    page = wide["pages"]["Quarterly spend ledger"]
    for table, where in ((sheet, "sheet"), (page, "page")):
        question = f"From the {table.document_title}, show the total amount for each department."
        intent = classify_question(
            question, providers=["notion", "google"], llm=_SaysPie(), fail_open=False,
            tables=[table],
        )
        assert intent.kind == "chart", where
        response = InsightsAgent().answer(
            question, wide["org"], viewer=SANA, user_id=wide["user"], spec=intent.spec,
        )
        assert _groups(response) == _round(BY_DEPT), where
        assert response.chart["chart"] == "pie"
    assert "Only the first tab of this Google Sheet is read." in InsightsAgent().answer(
        "sheet", wide["org"], viewer=SANA, user_id=wide["user"],
        spec=_spec(sheet, group="Region"),
    ).chart["caveat"]
