"""Slack charts: from the index, with the undercount said out loud.

Ingest stores THREADS, not messages, and short ones are dropped by
`SLACK_MIN_THREAD_CHARS` -- so these counts are conversations and they are a
FLOOR. Reading `conversations.history` live would be accurate but costs a
rate-limited call per channel per page load, so the honest cheap answer is the
index plus disclosure. That disclosure is asserted here, because a chart that
looks complete while undercounting is the failure that matters.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

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
            (f"slk-{uuid.uuid4().hex[:8]}",),
        ).fetchone()
        conn.commit()
    org_cleanup.append(str(row[0]))
    return str(row[0])


def _thread(org_id, *, channel="general", snippet="deploy is frozen",
            starter="ada"):
    """A Slack document exactly as the adapter leaves it."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents
                (org_id, title, source_provider, source_external_id,
                 source_last_modified, source_last_editor)
            VALUES (%s, %s, 'slack', %s, now(), %s)
            """,
            (org_id, f"#{channel}: {snippet}", uuid.uuid4().hex, starter),
        )
        conn.commit()


def _rows(org_id, **kw):
    return store.run_metric("slack_threads", org_id=org_id, workspace_id=None,
                            period="month", days=365, **kw)


def test_conversations_are_counted(org):
    _thread(org)
    _thread(org)
    facts.record_document_facts(org, provider="slack", workspace_id=None)
    assert sum(r.value for r in _rows(org)) == 2


def test_grouping_is_by_channel_not_by_thread_snippet(org):
    """A Slack document is titled "#general: deploy is frozen…". Grouping by
    that whole title gives one bar per thread, which is useless -- the channel
    is the dimension anyone actually wants."""
    _thread(org, channel="general", snippet="deploy is frozen")
    _thread(org, channel="general", snippet="standup moved")
    _thread(org, channel="design", snippet="new palette")
    facts.record_document_facts(org, provider="slack", workspace_id=None)

    by_channel = {}
    for row in _rows(org, group_by="subject"):
        by_channel[row.group] = by_channel.get(row.group, 0) + row.value
    assert by_channel == {"#general": 2.0, "#design": 1.0}


def test_the_thread_starter_is_credited(org):
    """The adapter already resolves the display name for the thread body and
    used to discard it on the listing path."""
    _thread(org, starter="ada")
    _thread(org, starter="ada")
    _thread(org, starter="grace")
    facts.record_document_facts(org, provider="slack", workspace_id=None)

    by_person = {}
    for row in _rows(org, group_by="actor"):
        by_person[row.group] = by_person.get(row.group, 0) + row.value
    assert by_person == {"ada": 2.0, "grace": 1.0}


def test_a_channel_rename_is_reflected_on_the_next_sync(org):
    """`source_config.channel_names` is a snapshot and every label downstream
    of it goes stale, which has bitten this codebase before. Facts are derived
    from the title on every sync, so a renamed channel corrects itself."""
    with get_connection() as conn:
        external = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO documents (org_id, title, source_provider,
                source_external_id, source_last_modified)
            VALUES (%s, '#old-name: hello', 'slack', %s, now())
            """,
            (org, external),
        )
        conn.commit()
    facts.record_document_facts(org, provider="slack", workspace_id=None)
    assert [r.group for r in _rows(org, group_by="subject")] == ["#old-name"]

    with get_connection() as conn:
        conn.execute(
            "UPDATE documents SET title = '#new-name: hello' "
            " WHERE source_external_id = %s",
            (external,),
        )
        conn.commit()
    facts.record_document_facts(org, provider="slack", workspace_id=None)
    assert [r.group for r in _rows(org, group_by="subject")] == ["#new-name"]


def test_notion_titles_are_not_mangled_by_the_slack_channel_extraction(org):
    """The extraction is per-provider. A Notion page called "Q3: goals" must
    keep its whole title, not be truncated to "Q3"."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO documents (org_id, title, source_provider,
                source_external_id, source_last_modified)
            VALUES (%s, 'Q3: goals', 'notion', %s, now())
            """,
            (org, uuid.uuid4().hex),
        )
        conn.commit()
    facts.record_document_facts(org, provider="notion", workspace_id=None)

    with get_connection() as conn:
        subject = conn.execute(
            "SELECT subject FROM activity_facts "
            " WHERE org_id = %s AND provider = 'notion'",
            (org,),
        ).fetchone()[0]
    assert subject == "Q3: goals"


def test_every_slack_metric_discloses_the_undercount():
    """A chart that looks complete while counting a floor is the failure that
    matters, and the disclosure is the only thing standing between the two."""
    from app.insights import registry

    for metric in registry.for_provider("slack"):
        assert metric.caveat, f"{metric.key} discloses nothing"
        text = metric.caveat.lower()
        assert "conversation" in text or "thread" in text
        assert "floor" in text or "not indexed" in text


def test_the_adapter_captures_the_thread_starter():
    """If this stops being set, every Slack "who starts them" chart goes
    silently empty -- the sync still succeeds, so nothing else notices."""
    import inspect

    from app.sources import slack

    source = inspect.getsource(slack.SlackAdapter._list_documents_from_slack)
    assert "last_editor" in source
    assert "_display_name" in source
