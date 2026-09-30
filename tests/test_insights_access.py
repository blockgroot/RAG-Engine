"""Charts respect document-level access (`insights.store._viewer_filter`).

Retrieval hid a document the asker could not open; a chart still COUNTED it and
its hover named the title. These pin that counts, hover rows, the subject list
a refusal repeats back, and "measured since" all go through the one visibility
predicate -- and that GitHub, which has no documents, is untouched.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from datetime import datetime, timezone

import pytest

from app.db import get_connection
from app.insights import store
from app.vectorstore.base import Viewer

from .conftest import requires_db

ADA = Viewer(email="ada@corp.com")
BO = Viewer(email="bo@corp.com")


def test_every_product_call_site_passes_a_viewer():
    """`viewer=None` means unrestricted, which is right for tests and wrong for
    a member. A chart call without one is how the gap would come back."""
    readers = {"run_metric", "list_facts", "list_subjects", "first_fact_at"}
    missing = []
    for path in pathlib.Path("app").rglob("*.py"):
        if path.name == "store.py" and path.parent.name == "insights":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in readers
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"store", "insight_store"}
                and not any(k.arg == "viewer" for k in node.keywords)
            ):
                missing.append(f"{path}:{node.lineno}")
    assert missing == []


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"chart-access-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _doc(org_id, provider, external_id, *, public, viewers=None, uri=None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents (org_id, title, source_uri, source_provider,
                                   source_external_id, doc_is_public, doc_viewers)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (org_id, f"doc {external_id}", uri, provider, external_id, public, viewers),
        )


def _fact(org_id, provider, kind, *, external_id, subject, url=None):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO activity_facts (org_id, provider, kind, subject, occurred_at,
                                        external_id, url)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (org_id, provider, kind, subject, datetime.now(timezone.utc), external_id, url),
        )


def _total(points) -> float:
    return sum(p.value for p in points)


@pytest.fixture
def drive(org):
    _doc(org, "google", "f-open", public=True)
    _doc(org, "google", "f-ada", public=False, viewers=["ada@corp.com"])
    _fact(org, "google", "doc_changed", external_id="f-open", subject="Handbook")
    _fact(org, "google", "doc_changed", external_id="f-ada", subject="Ada's review")
    # A fact whose document is gone: nothing left to check its sharing against.
    _fact(org, "google", "doc_changed", external_id="f-deleted", subject="Old plan")
    return org


def _kw(org):
    return dict(org_id=org, workspace_id=None, days=30)


@requires_db
def test_counts_include_only_documents_the_viewer_may_open(drive):
    run = lambda v: _total(store.run_metric("drive_docs_changed", period="month", viewer=v, **_kw(drive)))
    assert run(ADA) == 2
    assert run(BO) == 1
    assert run(Viewer.public_only_viewer()) == 1
    assert run(None) == 3  # unrestricted: internal callers, byte-identical SQL


@requires_db
def test_hover_rows_never_name_a_withheld_document(drive):
    subjects = {f.subject for f in store.list_facts("drive_docs_changed", viewer=BO, **_kw(drive))}
    assert subjects == {"Handbook"}


@requires_db
def test_the_subject_list_a_refusal_repeats_is_filtered(drive):
    assert store.list_subjects("drive_docs_changed", viewer=BO, **_kw(drive)) == ["Handbook"]
    assert store.list_subjects("drive_docs_changed", viewer=ADA, **_kw(drive)) == [
        "Ada's review", "Handbook",
    ]


@requires_db
def test_measured_since_ignores_withheld_facts(org):
    _doc(org, "google", "f-ada", public=False, viewers=["ada@corp.com"])
    _fact(org, "google", "doc_changed", external_id="f-ada", subject="x")
    assert store.first_fact_at("google", org_id=org, workspace_id=None, viewer=BO) is None
    assert store.first_fact_at("google", org_id=org, workspace_id=None, viewer=ADA) is not None


@requires_db
def test_linear_issue_facts_follow_their_issue_by_url(org):
    """Issue facts are keyed by identifier (ENG-7), the document by Linear's
    UUID -- the issue URL is the key both share."""
    _doc(org, "linear", "uuid-sec", public=False, viewers=["ada@corp.com"], uri="https://linear.app/x/issue/SEC-1")
    _doc(org, "linear", "uuid-pub", public=True, uri="https://linear.app/x/issue/ENG-7")
    _fact(org, "linear", "issue_completed", external_id="SEC-1", subject="Security",
          url="https://linear.app/x/issue/SEC-1")
    _fact(org, "linear", "issue_completed", external_id="ENG-7", subject="Eng",
          url="https://linear.app/x/issue/ENG-7")
    run = lambda v: _total(store.run_metric("issues_completed", period="month", viewer=v, **_kw(org)))
    assert run(ADA) == 2
    assert run(BO) == 1


@requires_db
def test_github_facts_have_no_documents_and_are_not_filtered(org):
    _fact(org, "github", "commit", external_id="sha1", subject="repo")
    assert _total(store.run_metric("commits_by_author", period="month", viewer=BO, **_kw(org))) == 1


@requires_db
def test_a_notion_fact_is_not_filtered(org):
    """Notion reports no sharing, so its documents are scope-public and its
    charts stay exactly as they were."""
    _fact(org, "notion", "doc_changed", external_id="p1", subject="page")
    assert _total(store.run_metric("docs_changed", period="month", viewer=BO, **_kw(org))) == 1
