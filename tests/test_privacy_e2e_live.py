"""End to end: no Aadhaar, PAN, card, mobile, IFSC or UPI value leaves the process.

LIVE: the real chat API, the real model (LLM_* in .env), the real remote
embedder and reranker, and the staging database. Every HTTP request leaving the
process is recorded and searched for the raw values; the answers must still
carry the real values back to the person.

Opt-in only (costs model quota and ~5 minutes):

    PRIVACY_E2E=1 pytest tests/test_privacy_e2e_live.py -s

Covered: uploads of .txt, .csv, .xlsx, .docx, .pdf and .md; an indexed document
(ingest, contextualization, embedding); a question that itself contains an
identifier; a look-alike (order id) that must NOT be masked.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest
from stdnum import verhoeff

from app.attachments import store as attach_store
from app.db import get_connection

from .conftest import requires_db
from .test_attachment_storage import FakeBlobStore

pytestmark = [
    requires_db,
    pytest.mark.skipif(os.getenv("PRIVACY_E2E") != "1", reason="live: set PRIVACY_E2E=1"),
]


def _aadhaar(body: str) -> str:
    return body + verhoeff.calc_check_digit(body)


# Synthetic values: each passes its real check digit / validity rule.
RAVI = {
    "mobile": "+91 98765 43210",
    "pan": "AJKPK4821Q",
    "ifsc": "HDFC0004521",
    "upi": "ravi.kumar@okhdfc",
    "aadhaar": _aadhaar("49183726501"),
}
PRIYA = {"mobile": "9123456780", "pan": "BQRPS7310L", "upi": "priya.s@ybl"}
MEENA = {"aadhaar": _aadhaar("73829104562"), "vid": _aadhaar("918273645501928"), "pan": "CMNPM5512D"}
ARJUN = {"card": "4111 1111 1111 1111", "mobile": "+91-99887-76655"}
INDEXED = {"mobile": "9988012345", "pan": "DXYPA9087K", "ifsc": "ICIC0001122"}
ORDER_ID = "5012345678"  # 10 digits, starts with 5: an order id, never masked

ALL_VALUES = [*RAVI.values(), *PRIYA.values(), *MEENA.values(), *ARJUN.values(), *INDEXED.values()]
SAFE_HOSTS = {"testserver"}  # our own app, called by the test client


def _spellings(value: str) -> set[str]:
    """The ways a value could appear in a payload."""
    digits = re.sub(r"\D", "", value)
    out = {value, value.lower(), value.upper()}
    if len(digits) >= 10:
        out.add(digits)
        out.add(digits[-10:])  # a mobile without +91
    return {s for s in out if len(s) >= 8}


LEAK_PATTERNS = {v: _spellings(v) for v in ALL_VALUES}


# -- fixtures -----------------------------------------------------------------------


@pytest.fixture(scope="module")
def outbound():
    """Record every request leaving the process (sync and async httpx, web search)."""
    sent: list[tuple[str, str]] = []
    real_send, real_async = httpx.Client.send, httpx.AsyncClient.send

    def _body(request) -> str:
        try:
            return request.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - a streaming body we cannot read
            return ""

    def spy(self, request, **kw):
        sent.append((request.url.host, _body(request)))
        return real_send(self, request, **kw)

    async def aspy(self, request, **kw):
        sent.append((request.url.host, _body(request)))
        return await real_async(self, request, **kw)

    from app.websearch import duckduckgo

    real_search = duckduckgo.DuckDuckGoSearch.search

    def search_spy(self, query, *a, **k):
        sent.append(("duckduckgo", query))
        return real_search(self, query, *a, **k)

    mp = pytest.MonkeyPatch()
    mp.setattr(httpx.Client, "send", spy)
    mp.setattr(httpx.AsyncClient, "send", aspy)
    mp.setattr(duckduckgo.DuckDuckGoSearch, "search", search_spy)
    yield sent
    mp.undo()


@pytest.fixture(scope="module")
def app_client(outbound):
    from fastapi.testclient import TestClient

    from app.api.main import create_app
    from app.auth import create_admin, create_session_token

    mp = pytest.MonkeyPatch()
    mp.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    mp.setenv("CHAT_STREAM_WORD_DELAY_MS", "0")
    mp.setattr(attach_store, "blobstore", FakeBlobStore())  # no uploads to Cloudinary

    with get_connection() as conn:
        org = str(conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"privacy-e2e-{uuid.uuid4().hex[:8]}",),
        ).fetchone()[0])
        conn.commit()
    email = f"sana.asiwal+privacy-e2e-{uuid.uuid4().hex[:6]}@syvora.com"
    user = create_admin(email, org)
    try:
        yield SimpleNamespace(
            client=TestClient(create_app()), cookies={"session": create_session_token(user)},
            org=org, user=user,
        )
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org,))
            conn.execute("DELETE FROM users WHERE email = %s", (email,))
            conn.commit()
        mp.undo()


# -- files --------------------------------------------------------------------------


def _txt() -> bytes:
    return (
        "Vendor onboarding record\n"
        "Vendor contact: Ravi Kumar\n"
        f"Mobile: {RAVI['mobile']}\n"
        f"PAN: {RAVI['pan']}\n"
        f"Bank IFSC: {RAVI['ifsc']}\n"
        f"UPI for payments: {RAVI['upi']}\n"
        f"Aadhaar (KYC): {RAVI['aadhaar'][:4]} {RAVI['aadhaar'][4:8]} {RAVI['aadhaar'][8:]}\n"
        f"Laptop order number: {ORDER_ID}\n"
        "Contact email: ravi.kumar@example.com\n"
    ).encode()


def _csv() -> bytes:
    return (
        "name,team,mobile,pan,upi\n"
        f"Priya Shah,Design,{PRIYA['mobile']},{PRIYA['pan']},{PRIYA['upi']}\n"
        "Karan Mehta,Sales,,,\n"
    ).encode()


def _xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Employee", "Aadhaar", "Virtual ID", "PAN"])
    ws.append(["Meena Iyer", MEENA["aadhaar"], MEENA["vid"], MEENA["pan"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _docx() -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph("Travel reimbursement for Arjun Rao.")
    doc.add_paragraph(f"Corporate card used: {ARJUN['card']}. Reach Arjun on {ARJUN['mobile']}.")
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _pdf() -> bytes:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    fig = plt.figure(figsize=(8.5, 3))
    fig.text(0.05, 0.7, f"KYC summary: Meena Iyer, PAN {MEENA['pan']}", fontsize=12)
    fig.text(0.05, 0.5, f"Aadhaar {MEENA['aadhaar']}", fontsize=12)
    buf = io.BytesIO()
    fig.savefig(buf, format="pdf")
    plt.close(fig)
    return buf.getvalue()


def _md() -> bytes:
    return f"# Support escalation\n\nCall the on-call lead Arjun at {ARJUN['mobile']}.\n".encode()


# -- helpers ------------------------------------------------------------------------


def _conversation(ctx) -> str:
    with get_connection() as conn:
        conv = str(conn.execute(
            "INSERT INTO conversations (org_id, user_id) VALUES (%s, %s) RETURNING id",
            (ctx.org, ctx.user.id),
        ).fetchone()[0])
        conn.commit()
    return conv


def _upload(ctx, conv: str, files: list[tuple[str, bytes]]) -> dict:
    r = ctx.client.post(
        f"/chat/conversations/{conv}/attachments",
        files=[("file", (name, body, "application/octet-stream")) for name, body in files],
        cookies=ctx.cookies,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _ask(ctx, question: str, conv: str | None = None) -> dict:
    body = {"question": question}
    if conv:
        body["conversation_id"] = conv
    r = ctx.client.post("/chat/stream", json=body, cookies=ctx.cookies)
    assert r.status_code == 200, r.text
    done = {}
    for block in r.text.strip().split("\n\n"):
        lines = block.split("\n")
        if lines and lines[0] in ("event: done", "event: error"):
            data = json.loads("\n".join(l[6:] for l in lines[1:] if l.startswith("data: ")))
            done = data if lines[0] == "event: done" else {"answer": "", "error": data.get("message")}
    time.sleep(12)  # the free model tier allows ~15 requests a minute
    return done


def _leaks(sent: list[tuple[str, str]]) -> list[tuple[str, str]]:
    found = []
    for host, body in sent:
        if host in SAFE_HOSTS:
            continue
        for value, spellings in LEAK_PATTERNS.items():
            if any(s in body for s in spellings):
                found.append((host, value))
    return found


def _has(answer: str, value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 10:
        return digits[-10:] in re.sub(r"\D", "", answer)
    return value.lower() in answer.lower()


# -- the run ------------------------------------------------------------------------


REPORT: list[tuple[str, str, str]] = []


def test_uploads_and_questions_end_to_end(app_client, outbound):
    ctx = app_client
    conv = _conversation(ctx)
    up = _upload(ctx, conv, [
        ("vendor.txt", _txt()), ("team.csv", _csv()), ("kyc.xlsx", _xlsx()),
        ("travel.docx", _docx()), ("kyc.pdf", _pdf()),
    ])
    assert len(up["attachments"]) == 5 and not up["rejected"], up
    REPORT.append(("upload", "5 files saved (.txt .csv .xlsx .docx .pdf)", ""))

    cases = [
        ("What is Ravi Kumar's mobile number?", [RAVI["mobile"]]),
        ("What are Ravi Kumar's PAN and bank IFSC code?", [RAVI["pan"], RAVI["ifsc"]]),
        (f"Whose UPI ID is {RAVI['upi']}?", ["Ravi"]),
        ("What is the laptop order number for Ravi?", [ORDER_ID]),
        ("What is Priya Shah's mobile number and UPI ID in the team sheet?", [PRIYA["mobile"], PRIYA["upi"]]),
        ("What is Meena Iyer's PAN?", [MEENA["pan"]]),
        ("How can I reach Arjun Rao, and which card did he use for travel?", [ARJUN["mobile"]]),
        ("What is Ravi Kumar's contact email?", ["ravi.kumar@example.com"]),
    ]
    for question, expected in cases:
        done = _ask(ctx, question, conv)
        answer = done.get("answer") or ""
        ok = all(_has(answer, e) for e in expected)
        REPORT.append((question, "OK" if ok else "MISSING " + ", ".join(e for e in expected if not _has(answer, e)),
                       answer[:220] or f"ERROR: {done.get('error')}"))

    leaks = _leaks(outbound)
    assert not leaks, f"raw identifiers left the process: {leaks}"
    ai_calls = [h for h, _ in outbound if h not in SAFE_HOSTS]
    assert ai_calls, "no outbound calls recorded: the run never reached the model"
    assert any("<PHONE_" in b for _, b in outbound), "masking never engaged"


def test_indexed_document_end_to_end(app_client, outbound):
    from app.ingestion.pipeline import ingest_source
    from app.sources.base import DocAccess, SourceDocument, SourceRef
    from datetime import datetime, timezone

    ctx = app_client
    when = datetime(2026, 10, 1, tzinfo=timezone.utc)
    text = (
        "Facilities vendor directory.\n"
        f"The office caterer Anil Desai can be reached on {INDEXED['mobile']}. "
        f"Invoices go to PAN {INDEXED['pan']}, paid to IFSC {INDEXED['ifsc']}."
    )

    class _Adapter:
        def list_documents(self):
            return [SourceRef("vendors-1", "Facilities vendor directory", last_modified=when)]

        def fetch_document(self, external_id):
            return SourceDocument(
                external_id="vendors-1", title="Facilities vendor directory", content=text,
                source_uri="https://example.com/vendors-1", last_modified=when,
                access=DocAccess.scope_public(),
            )

        def get_last_modified(self, external_id):
            return when

    before = len(outbound)
    ingest_source(_Adapter(), ctx.org, provider="notion")
    ingest_leaks = _leaks(outbound[before:])
    assert not ingest_leaks, f"ingest sent raw identifiers: {ingest_leaks}"
    REPORT.append(("ingest", f"{len(outbound) - before} outbound calls", "no leaks"))

    for question, expected in [
        ("What is the phone number of the office caterer Anil Desai?", [INDEXED["mobile"]]),
        (f"Who has the PAN {INDEXED['pan']}?", ["Anil"]),
    ]:
        done = _ask(ctx, question)
        answer = done.get("answer") or ""
        ok = all(_has(answer, e) for e in expected)
        REPORT.append((question, "OK" if ok else "MISSING " + ", ".join(e for e in expected if not _has(answer, e)),
                       answer[:220] or f"ERROR: {done.get('error')}"))

    leaks = _leaks(outbound)
    assert not leaks, f"raw identifiers left the process: {leaks}"


def test_markdown_upload_end_to_end(app_client, outbound):
    ctx = app_client
    conv = _conversation(ctx)
    up = _upload(ctx, conv, [("oncall.md", _md())])
    assert len(up["attachments"]) == 1, up
    done = _ask(ctx, "What number should I call to reach the on-call lead?", conv)
    answer = done.get("answer") or ""
    REPORT.append(("[.md] on-call number", "OK" if _has(answer, ARJUN["mobile"]) else "MISSING",
                   answer[:220] or f"ERROR: {done.get('error')}"))
    assert not _leaks(outbound), _leaks(outbound)


def test_zz_report(outbound):
    hosts: dict[str, int] = {}
    for h, _ in outbound:
        hosts[h] = hosts.get(h, 0) + 1
    print("\n\n=== Outbound calls by host ===")
    for h, n in sorted(hosts.items()):
        print(f"  {h}: {n}")
    print("\n=== Questions ===")
    for q, status, answer in REPORT:
        print(f"- {q}\n    {status}\n    {answer!r}")
