"""Facts are derived from what ingest already stored, so a chart can never show
something the index does not contain.

The writer is one INSERT ... SELECT over `documents`: the rows are already in
this database, so pulling them into Python to push them back would be slower
and would invent a failure mode (a half-written batch) that SQL does not have.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.db import get_connection
from app.insights import facts, store
from .conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def org(org_cleanup):
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
            (f"facts-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _document(org_id, *, provider="notion", workspace_id=None, modified="now",
              editor=None, title="A page"):
    """One `documents` row, as ingest would have left it."""
    when = (
        datetime.now(timezone.utc) if modified == "now"
        else None if modified is None
        else modified
    )
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents
                (org_id, title, source_provider, source_external_id,
                 source_last_modified, workspace_id, source_last_editor)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (org_id, title, provider, uuid.uuid4().hex, when, workspace_id, editor),
        )
        conn.commit()


def _count(org_id, provider="notion") -> float:
    rows = store.run_metric(
        "docs_changed" if provider == "notion" else "drive_docs_changed",
        org_id=org_id, workspace_id=None, period="month", days=365,
    )
    return sum(r.value for r in rows)


def test_a_document_becomes_one_fact(org):
    _document(org)
    assert facts.record_document_facts(org, provider="notion", workspace_id=None) == 1
    assert _count(org) == 1


def test_re_recording_the_same_document_does_not_double_the_count(org):
    """Every sync re-lists every document, so this runs constantly. The unique
    index on external_id is the guard; this proves the writer relies on it
    (ON CONFLICT) instead of inserting blindly."""
    _document(org)
    facts.record_document_facts(org, provider="notion", workspace_id=None)
    facts.record_document_facts(org, provider="notion", workspace_id=None)
    assert _count(org) == 1


def test_an_edit_moves_the_fact_rather_than_adding_one(org):
    """A page edited five times is one page. `source_last_modified` is the
    latest edit we know of, so the fact's date follows it -- otherwise every
    re-sync would either duplicate the page or freeze it on its first date."""
    with get_connection() as conn:
        external = uuid.uuid4().hex
        old = datetime.now(timezone.utc) - timedelta(days=200)
        conn.execute(
            """
            INSERT INTO documents (org_id, title, source_provider,
                source_external_id, source_last_modified)
            VALUES (%s, 'A page', 'notion', %s, %s)
            """,
            (org, external, old),
        )
        conn.commit()
    facts.record_document_facts(org, provider="notion", workspace_id=None)

    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET source_last_modified = now() WHERE source_external_id = %s",
            (external,),
        )
        conn.commit()
    facts.record_document_facts(org, provider="notion", workspace_id=None)

    # Still one page, and it now sits inside a 30-day window.
    assert _count(org) == 1
    recent = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                              period="month", days=30)
    assert sum(r.value for r in recent) == 1


def test_a_document_with_no_last_modified_is_skipped_not_dated_now(org):
    """Stamping now() would invent an edit that never happened, and would pile
    every undated document onto today's bar."""
    _document(org, modified=None)
    assert facts.record_document_facts(org, provider="notion", workspace_id=None) == 0
    assert _count(org) == 0


def test_a_space_s_documents_stay_in_that_space(org):
    """The fact inherits the document's workspace_id, so a space's chart counts
    only its own pages -- the scope is carried by the data, not re-derived."""
    with get_connection() as conn:
        user = conn.execute(
            "INSERT INTO users (email, org_id, role) VALUES (%s, %s, 'member') RETURNING id",
            (f"{uuid.uuid4().hex[:8]}@example.com", org),
        ).fetchone()
        space = conn.execute(
            "INSERT INTO workspaces (org_id, name, created_by) VALUES (%s, 'Notes', %s) RETURNING id",
            (org, user[0]),
        ).fetchone()
        conn.commit()
    space_id = str(space[0])

    _document(org, workspace_id=None)
    _document(org, workspace_id=space_id)
    facts.record_document_facts(org, provider="notion", workspace_id=None)
    facts.record_document_facts(org, provider="notion", workspace_id=space_id)

    org_rows = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                                period="month", days=365)
    space_rows = store.run_metric("docs_changed", org_id=org, workspace_id=space_id,
                                  period="month", days=365)
    assert sum(r.value for r in org_rows) == 1
    assert sum(r.value for r in space_rows) == 1


def test_another_providers_documents_are_not_recorded(org):
    """The writer takes an explicit provider for the same reason every sync path
    does: without it, the first Drive run would claim every Notion page."""
    _document(org, provider="notion")
    _document(org, provider="google")
    assert facts.record_document_facts(org, provider="google", workspace_id=None) == 1
    assert _count(org, provider="google") == 1
    assert _count(org, provider="notion") == 0


def test_github_is_never_recorded_from_documents(org):
    """GitHub has no `documents` rows at all -- it embeds nothing. Reading them
    for GitHub would silently return zero forever, which looks like "no
    activity" rather than "wrong code path", so it raises instead."""
    with pytest.raises(ValueError):
        facts.record_document_facts(org, provider="github", workspace_id=None)


# ---------------------------------------------------------------------------
# The worker hook. Facts are recorded where a successful ingest is already
# known to have happened -- next to the answer-cache clear, for the same reason.
# ---------------------------------------------------------------------------


def test_the_worker_hook_records_facts_for_a_document_provider(org):
    from app.jobs.worker import _record_insight_facts

    _document(org)
    _record_insight_facts(org, "notion", None)
    assert _count(org) == 1


def test_the_worker_hook_skips_github_without_raising(org):
    """GitHub reaches this hook on every successful job it can have, and has
    nothing to count. It must return quietly -- raising here would fail an
    ingest that already succeeded."""
    from app.jobs.worker import _record_insight_facts

    _record_insight_facts(org, "github", None)  # must not raise


def test_a_failing_facts_write_never_fails_the_job(org, monkeypatch, caplog):
    """The whole reason it has its own try/except: a stale chart is a stale
    chart, but failing a finished job turns it into a retry loop over work that
    is already done."""
    from app.jobs import worker

    def _boom(*args, **kwargs):
        raise RuntimeError("postgres went away")

    monkeypatch.setattr(worker, "record_document_facts", _boom)

    with caplog.at_level("WARNING"):
        worker._record_insight_facts(org, "notion", None)  # must not raise

    assert any("could not record" in r.message for r in caplog.records)


def test_the_editor_is_carried_into_the_fact_so_a_leaderboard_is_a_group_by(org):
    """"Top editors" only works because the name was captured at sync time.
    Fetching it per page load would be an API call per chart render."""
    _document(org, editor="Ada Lovelace")
    _document(org, editor="Ada Lovelace")
    _document(org, editor="Grace Hopper")
    facts.record_document_facts(org, provider="notion", workspace_id=None)

    rows = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                            period="month", days=365, group_by="actor")
    by_editor = {}
    for row in rows:
        by_editor[row.group] = by_editor.get(row.group, 0) + row.value
    assert by_editor == {"Ada Lovelace": 2.0, "Grace Hopper": 1.0}


def test_a_document_with_no_known_editor_still_counts(org):
    """Drive omits `lastModifyingUser` for some files and Notion's lookup can
    fail. The page is still real, so it must appear in the count -- just not in
    the editor breakdown, where a guessed name would be worse than a gap."""
    _document(org, editor=None)
    facts.record_document_facts(org, provider="notion", workspace_id=None)

    assert _count(org) == 1
    rows = store.run_metric("docs_changed", org_id=org, workspace_id=None,
                            period="month", days=365, group_by="actor")
    assert [r.group for r in rows] == [None]


# ---------------------------------------------------------------------------
# The adapters. Both capture the editor from a request they ALREADY make, which
# is the whole reason "top editors" costs no extra API call.
# ---------------------------------------------------------------------------


def test_drive_asks_for_the_editor_in_the_listing_it_already_makes():
    """If this field ever leaves `_LIST_FIELDS`, every Drive editor chart goes
    silently empty -- the sync still succeeds, so nothing else would notice."""
    from app.sources import google_drive

    assert "lastModifyingUser(displayName)" in google_drive._LIST_FIELDS


def test_drive_reads_the_editor_name_off_a_file():
    from app.sources.google_drive import _editor_name

    assert _editor_name({"lastModifyingUser": {"displayName": "Ada"}}) == "Ada"


def test_drive_returns_none_rather_than_attributing_an_unknown_editor():
    """Drive omits the field for service-account edits, deleted accounts and
    some shared drives. None keeps the row out of the chart; a placeholder
    would credit the work to a person who did not do it."""
    from app.sources.google_drive import _editor_name

    assert _editor_name({}) is None
    assert _editor_name({"lastModifyingUser": {}}) is None
    assert _editor_name({"lastModifyingUser": {"displayName": "  "}}) is None
