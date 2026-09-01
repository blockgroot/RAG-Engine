"""Reports read from our own index, not the source's API.

Real Postgres, no network — which is the point: the whole feature is one query
plus its disclosures, so a fake store would test nothing that can break.

The isolation tests here matter more than the equivalents on the old live path.
Live fetchers were scoped by which *credential* they resolved; an indexed read
is scoped by a WHERE clause, so a missing predicate leaks another space's or
another tenant's documents into a report rather than merely failing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.auth import OAuthTokens, save_connection
from app.auth.users import invite_member
from app.core.exceptions import ConfigurationError
from app.schedulers import activity
from app.workspaces.store import create_workspace

from .conftest import requires_db

NOW = datetime.now(timezone.utc)
SINCE = NOW - timedelta(days=7)

#: chunks.embedding is NOT NULL and vector(1024); content is what we assert on.
_VEC = [0.0] * 1024


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


@pytest.fixture
def org(store, org_cleanup):
    org_id = store.create_organization(f"Indexed Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    return org_id


def _connect(org_id: str, provider: str, workspace_id: str | None = None) -> None:
    save_connection(
        org_id,
        provider,
        OAuthTokens(
            access_token=f"tok-{uuid.uuid4().hex[:6]}",
            refresh_token=None,
            expires_at=None,
            external_workspace_id=f"ext-{uuid.uuid4().hex[:6]}",
        ),
        workspace_id=workspace_id,
    )


def _doc(
    store,
    org_id: str,
    *,
    provider: str = "notion",
    title: str = "Pricing",
    chunks: list[str] | None = None,
    changed: datetime | None = None,
    uri: str | None = "https://notion.so/pricing",
    workspace_id: str | None = None,
) -> str:
    chunks = chunks or ["Refunds are handled within 14 days."]
    return store.upsert_source_document(
        org_id,
        provider=provider,
        external_id=f"ext-{uuid.uuid4().hex[:10]}",
        title=title,
        chunks=chunks,
        embeddings=[_VEC for _ in chunks],
        source_uri=uri,
        last_modified=changed or (NOW - timedelta(days=1)),
        workspace_id=workspace_id,
    )


def _fetch(org_id: str, provider: str = "notion", workspace_id=None):
    return activity.fetch_indexed_activity(
        org_id, SINCE, provider=provider, workspace_id=workspace_id
    )


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------


@requires_db
def test_only_documents_changed_in_the_window_are_reported(store, org):
    _connect(org, "notion")
    _doc(store, org, title="Changed", changed=NOW - timedelta(days=1))
    _doc(store, org, title="Old", changed=NOW - timedelta(days=30))

    digest = _fetch(org)

    titles = " ".join(item.meta for item in digest.items)
    assert "Changed" in titles
    assert "Old" not in titles


@requires_db
def test_a_document_with_no_source_timestamp_is_never_reported(store, org):
    """`source_last_modified IS NULL` cannot be placed in any window. Including
    it would put a manually-ingested file into every report forever."""
    _connect(org, "notion")
    _doc(store, org, title="Undated", changed=None)
    with __import__("app.db.connection", fromlist=["x"]).get_connection() as conn:
        conn.execute(
            "UPDATE documents SET source_last_modified = NULL WHERE org_id = %s",
            (org,),
        )

    assert _fetch(org).items == ()


@requires_db
def test_content_arrives_in_chunk_order(store, org):
    """A multi-chunk page must read as a document, not shuffled fragments."""
    _connect(org, "notion")
    _doc(store, org, chunks=["First part.", "Second part.", "Third part."])

    text = _fetch(org).items[0].summary
    assert text.index("First") < text.index("Second") < text.index("Third")


# --------------------------------------------------------------------------
# Isolation — a WHERE clause is now the only thing enforcing scope
# --------------------------------------------------------------------------


@requires_db
def test_another_orgs_documents_never_appear(store, org, org_cleanup):
    other = store.create_organization(f"Indexed Other {uuid.uuid4().hex[:8]}")
    org_cleanup.append(other)
    _connect(org, "notion")
    _doc(store, other, title="Other tenant secret")
    _doc(store, org, title="Mine")

    metas = " ".join(item.meta for item in _fetch(org).items)
    assert "Mine" in metas
    assert "Other tenant secret" not in metas


@requires_db
def test_a_space_report_never_reads_org_wide_documents(store, org):
    """A space sees ONLY its own rows — never also the org-wide ones. A
    meeting-notes space blending in company docs makes membership meaningless
    (CLAUDE.md §3)."""
    owner = invite_member(f"o-{uuid.uuid4().hex[:8]}@example.com", org)
    space = create_workspace(org, "Meeting notes", owner.id)
    _connect(org, "notion")
    _connect(org, "notion", workspace_id=space)
    _doc(store, org, title="Company handbook")
    _doc(store, org, title="Space minutes", workspace_id=space)

    metas = " ".join(item.meta for item in _fetch(org, workspace_id=space).items)
    assert "Space minutes" in metas
    assert "Company handbook" not in metas


@requires_db
def test_an_org_wide_report_never_reads_a_spaces_documents(store, org):
    """The mirror direction: NULL workspace_id must not match a space's rows."""
    owner = invite_member(f"o-{uuid.uuid4().hex[:8]}@example.com", org)
    space = create_workspace(org, "Meeting notes", owner.id)
    _connect(org, "notion")
    _doc(store, org, title="Company handbook")
    _doc(store, org, title="Space minutes", workspace_id=space)

    metas = " ".join(item.meta for item in _fetch(org).items)
    assert "Company handbook" in metas
    assert "Space minutes" not in metas


@requires_db
def test_providers_are_partitioned(store, org):
    """A Notion report including Slack threads would be the same class of bug
    as the un-partitioned sync that deleted every Notion doc (CLAUDE.md §3)."""
    _connect(org, "notion")
    _connect(org, "slack")
    _doc(store, org, provider="notion", title="A page")
    _doc(store, org, provider="slack", title="#general: a thread")

    metas = " ".join(item.meta for item in _fetch(org, "notion").items)
    assert "A page" in metas
    assert "#general" not in metas


# --------------------------------------------------------------------------
# A misconfiguration must not look like a quiet week
# --------------------------------------------------------------------------


@requires_db
def test_an_unconnected_provider_raises_rather_than_reporting_nothing(store, org):
    """Reading the index directly would make "not connected" and "nothing
    happened" indistinguishable, so a broken scheduler would mail quiet-period
    reports forever."""
    _doc(store, org, title="Orphaned page")

    with pytest.raises(ConfigurationError):
        _fetch(org)


@requires_db
def test_a_space_without_the_connection_raises_instead_of_falling_back(store, org):
    """Connected org-wide but NOT in the space: the space's report must fail,
    never quietly report the company's documents to a space member."""
    owner = invite_member(f"o-{uuid.uuid4().hex[:8]}@example.com", org)
    space = create_workspace(org, "Meeting notes", owner.id)
    _connect(org, "notion")  # org-wide only

    with pytest.raises(ConfigurationError):
        _fetch(org, workspace_id=space)


# --------------------------------------------------------------------------
# Disclosure — the reader must never mistake this for a live read
# --------------------------------------------------------------------------


@requires_db
def test_coverage_always_states_that_this_is_indexed_and_when_it_synced(store, org):
    """The load-bearing disclosure. An indexed report is only as current as the
    last sync, and a report implying live coverage it lacks is the exact
    failure the coverage-note rule exists to prevent."""
    _connect(org, "notion")
    _doc(store, org)

    notes = " ".join(_fetch(org).notes)
    assert "Read from indexed content" in notes
    assert "last synced" in notes


@requires_db
def test_coverage_discloses_the_sync_time_even_when_nothing_changed(store, org):
    """A quiet report is exactly when "when did this last sync?" matters most —
    it is the difference between "nothing happened" and "nothing was fetched"."""
    _connect(org, "notion")

    digest = _fetch(org)
    assert digest.items == ()
    assert any("last synced" in note for note in digest.notes)


@requires_db
def test_a_never_synced_connection_says_never(store, org):
    _connect(org, "notion")
    _doc(store, org)

    assert any("last synced never" in note for note in _fetch(org).notes)


@requires_db
def test_coverage_counts_documents_in_the_providers_own_unit(store, org):
    """"12 threads" and "12 files" are different claims — a reader should not
    have to guess what was counted."""
    _connect(org, "slack")
    _doc(store, org, provider="slack", title="#general: hello")

    assert any("1 thread changed" in note for note in _fetch(org, "slack").notes)


@requires_db
def test_hitting_the_document_cap_is_disclosed(store, org, monkeypatch):
    """Truncation that looks complete is the failure that matters. Ordered
    newest-first, so the cap drops the oldest end of the window."""
    monkeypatch.setattr(activity, "MAX_INDEXED_DOCS", 2)
    _connect(org, "notion")
    for i in range(3):
        _doc(store, org, title=f"Page {i}", changed=NOW - timedelta(hours=i + 1))

    digest = _fetch(org)
    assert len(digest.items) == 2
    assert any("most recently changed" in note for note in digest.notes)


# --------------------------------------------------------------------------
# Item shape — the report page and email render from this
# --------------------------------------------------------------------------


@requires_db
def test_items_carry_the_source_link_and_never_put_it_in_the_summary(store, org):
    """The model is never asked to write a URL, so the page renders it from
    here — a fabricated link is impossible rather than discouraged."""
    _connect(org, "notion")
    _doc(store, org, uri="https://notion.so/a-page")

    item = _fetch(org).items[0]
    assert item.url == "https://notion.so/a-page"
    assert "http" not in item.summary


@requires_db
def test_meta_carries_the_title_and_a_human_timestamp(store, org):
    """meta is attribution, summary is content — the page lays them out as
    separate rows, so they must not be merged here."""
    _connect(org, "notion")
    _doc(store, org, title="Pricing", chunks=["Refund terms."])

    item = _fetch(org).items[0]
    assert item.meta.startswith("Pricing · ")
    assert "Refund terms." in item.summary
    assert "Pricing ·" not in item.summary


@requires_db
def test_a_document_with_no_uri_still_reports(store, org):
    """A missing link must cost the link, not the item."""
    _connect(org, "notion")
    _doc(store, org, uri=None)

    item = _fetch(org).items[0]
    assert item.url is None
    assert item.summary


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


@requires_db
def test_slack_refreshes_channel_labels_before_reading(store, org, monkeypatch):
    """Slack titles are "#channel: snippet", so without this a rename would
    show the old name in this report's snapshotted items forever."""
    _connect(org, "slack")
    _doc(store, org, provider="slack", title="#old-name: hello")

    called: list[tuple] = []
    monkeypatch.setattr(
        activity_slack_utils := __import__(
            "app.sources.slack_utils", fromlist=["refresh_channel_names"]
        ),
        "refresh_channel_names",
        lambda org_id, workspace_id=None: called.append((org_id, workspace_id)) or [],
    )

    _fetch(org, "slack")
    assert called == [(org, None)]


@requires_db
def test_github_is_not_routed_through_the_index(store, org):
    """GitHub embeds nothing, so there is no index to read — and it has a real
    list_commits(since=). Its fetcher must stay the live one."""
    assert activity._FETCHERS["github"] is activity.fetch_github_activity
    indexed = {
        p for p, f in activity._FETCHERS.items() if f is not activity.fetch_github_activity
    }
    assert indexed == {"slack", "linear", "notion", "google"}
