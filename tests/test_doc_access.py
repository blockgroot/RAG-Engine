"""Document-level access filtering: what one member may retrieve, one level
below the space boundary `test_isolation.py` pins.

Everything here is the smallest thing that fails if the mechanism breaks:

* the two halves of hybrid retrieval agree (a document hidden from the vector
  leg must not arrive through BM25);
* ingest fails CLOSED for an ACL-capable provider that cannot read sharing;
* revocation reaches an unchanged document, which is the case a content-based
  sync can never notice on its own;
* a withheld document produces the "ask for access" refusal, and a genuinely
  empty corpus does NOT (that is the claim that must never be invented);
* the answer cache refuses to store an answer built from a restricted document.

The SQL itself needs a database, so the query tests are skipped without one;
the write-side, spelling and decision tests run anywhere.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.sources.base import DocAccess, SourceDocument, SourceRef
from app.sources.factory import ACL_CAPABLE
from app.vectorstore.base import RetrievedChunk, Viewer
from app.vectorstore.pgvector_store import _normalize_viewers, _viewer_clause


# -- the two sides must spell an entry identically -------------------------


def test_read_and_write_spell_an_entry_the_same_way():
    """`Viewer.acl` and `_normalize_viewers` are the only two formatters, and a
    grant that is spelled differently on the two sides silently stops matching."""
    written = _normalize_viewers(["  Ada@Example.COM ", "ada@example.com", ""])
    assert written == ["ada@example.com"]
    assert "ada@example.com" in Viewer(email="Ada@Example.com").acl()


def test_viewer_acl_includes_the_domain_entry():
    """Drive reports domain-wide shares as a domain, not as a list of people."""
    assert Viewer(email="ada@example.com").acl() == [
        "ada@example.com",
        "domain:example.com",
    ]


def test_unrestricted_viewer_adds_no_sql_at_all():
    """Not "a clause that matches everything" -- the literal absence of one, so
    every read that predates this feature is byte-identical."""
    assert _viewer_clause(None) == ("", [])
    assert _viewer_clause(Viewer.unrestricted()) == ("", [])
    assert _viewer_clause(Viewer(email="")) == ("", [])


def test_a_signed_in_viewer_with_no_email_fails_closed():
    """`deps.viewer_for` returns this when the users row cannot be read. It must
    behave as "scope-public only", never as "unrestricted"."""
    sql, params = _viewer_clause(Viewer(email="ada@example.com"))
    assert "doc_is_public" in sql and "doc_viewers &&" in sql
    assert params == [["ada@example.com", "domain:example.com"]]


# -- ingest fails closed ----------------------------------------------------


def _access_for(doc, ref, provider):
    from app.ingestion.pipeline import _doc_access

    return _doc_access(doc, ref, provider)


def _doc(**kw):
    base = dict(external_id="f1", title="T", content="c")
    base.update(kw)
    return SourceDocument(**base)


def test_acl_capable_provider_with_no_sharing_info_is_skipped():
    """The whole fail-closed rule. Drive omits `permissions` when the connecting
    account cannot read them -- indexing anyway would publish, to the entire
    space, exactly the file whose sharing we could not read."""
    assert "google" in ACL_CAPABLE
    assert _access_for(_doc(), SourceRef(external_id="f1", title="T"), "google") is None


def test_a_provider_that_cannot_report_sharing_keeps_scope_visibility():
    """Notion has no per-page permission API. It must keep working exactly as
    before, not go dark."""
    assert "notion" not in ACL_CAPABLE
    assert _access_for(_doc(), SourceRef(external_id="f1", title="T"), "notion") == (
        True,
        None,
        False,
    )


def test_sharing_from_the_listing_is_used_when_the_fetch_has_none():
    """Drive answers from `files.list` for free; the fetch need not repeat it."""
    ref = SourceRef(
        external_id="f1", title="T", access=DocAccess.restricted(["ada@example.com"])
    )
    assert _access_for(_doc(), ref, "google") == (False, ["ada@example.com"], False)


def test_the_fetched_document_wins_over_the_listing():
    """Both may report; the fetch is the fresher read."""
    ref = SourceRef(external_id="f1", title="T", access=DocAccess.scope_public())
    doc = _doc(access=DocAccess.restricted(["ada@example.com"]))
    assert _access_for(doc, ref, "google") == (False, ["ada@example.com"], False)


def test_drive_permissions_are_translated_by_grant_type():
    from app.sources.google_drive import _file_access

    access = _file_access(
        {
            "permissions": [
                {"type": "user", "emailAddress": "Ada@Example.com"},
                {"type": "domain", "domain": "Example.com"},
                {"type": "group", "emailAddress": "eng@example.com"},
                {"type": "user", "emailAddress": "gone@example.com", "deleted": True},
            ]
        }
    )
    assert access is not None and access.is_public is False
    assert set(access.viewers) == {
        "ada@example.com",
        "domain:example.com",
        "group:eng@example.com",
    }


def test_anyone_with_the_link_is_public_within_the_scope():
    from app.sources.google_drive import _file_access

    access = _file_access({"permissions": [{"type": "anyone"}]})
    assert access is not None and access.is_public is True


def test_missing_permissions_field_is_not_public():
    """`None` means "we could not read sharing", which is not the same fact as
    "shared with everyone" -- conflating them is the leak."""
    from app.sources.google_drive import _file_access

    assert _file_access({"name": "x"}) is None


# -- revocation -------------------------------------------------------------


class _RecordingStore:
    def __init__(self):
        self.calls: list[dict] = []

    def set_source_document_access(self, org_id, *, provider, entries, workspace_id=None):
        self.calls.append({"org_id": org_id, "provider": provider, "entries": entries})
        return len(entries)


def test_unchanged_documents_have_their_access_refreshed():
    """A permission change moves no content, so an unshared file is classed
    "unchanged" and never re-fetched. Without this, revocation would land only
    if somebody also happened to edit the file."""
    from app.ingestion.pipeline import _restamp_unchanged_access

    store = _RecordingStore()
    refs = [
        SourceRef(external_id="kept", title="A", access=DocAccess.restricted(["a@x.com"])),
        SourceRef(external_id="fetched", title="B", access=DocAccess.scope_public()),
    ]
    frozen = _restamp_unchanged_access(
        store, refs, {"fetched"}, org_id="o", provider="google", workspace_id=None
    )
    # Nothing was frozen: every un-refetched ref reported its sharing.
    assert frozen == 0
    assert store.calls[0]["entries"] == [("kept", False, ["a@x.com"])]


def test_restamping_is_skipped_for_providers_that_report_no_acl():
    from app.ingestion.pipeline import _restamp_unchanged_access

    store = _RecordingStore()
    refs = [SourceRef(external_id="p", title="A")]
    assert _restamp_unchanged_access(
        store, refs, set(), org_id="o", provider="notion", workspace_id=None
    ) == 0
    assert store.calls == []


# -- the cache must not serve one person's access situation to another ------


def test_an_answer_from_a_restricted_document_is_not_cached():
    """`query_answer_cache` has no viewer in its key, deliberately. So the WRITE
    is gated instead: an answer built from a document not readable by the whole
    scope would otherwise be served verbatim to someone who cannot open it."""
    from app.rag.pipeline import RagResult, _is_cacheable

    public_hit = RetrievedChunk(
        content="c", score=0.9, document_id="d", chunk_index=0, org_id="o",
        doc_is_public=True,
    )
    private_hit = RetrievedChunk(
        content="c", score=0.9, document_id="d", chunk_index=0, org_id="o",
        doc_is_public=False,
    )
    assert _is_cacheable(RagResult(answer="a", answered=True, sources=[public_hit]))
    assert not _is_cacheable(RagResult(answer="a", answered=True, sources=[private_hit]))


def test_a_not_shared_with_you_refusal_is_not_cached():
    """It is a statement about ONE person's access, and wrong for everyone else."""
    from app.rag.pipeline import RagResult, _is_cacheable

    assert not _is_cacheable(
        RagResult(answer="ask the owner", answered=False, access_restricted=True)
    )


# -- the refusal says the true thing, and only when it is true --------------


def test_the_notice_names_the_connector_and_never_a_document():
    """Naming the connected folder is safe -- every space MEMBER already sees it
    on the space page. Naming the document that matched is the leak itself."""
    from app.rag import access_notice

    text = access_notice.restricted_notice.__wrapped__ if hasattr(
        access_notice.restricted_notice, "__wrapped__"
    ) else None
    del text  # no wrapper; the real check is below

    original = access_notice._connected_scope_name
    access_notice._connected_scope_name = lambda *a, **k: "Meeting notes"
    try:
        message = access_notice.restricted_notice(
            "google", org_id="o", workspace_id="w"
        )
    finally:
        access_notice._connected_scope_name = original

    assert "Meeting notes" in message
    assert "Google Drive" in message
    assert "the owner of this space" in message


def test_the_notice_asks_an_admin_when_the_scope_is_company_wide():
    from app.rag import access_notice

    original = access_notice._connected_scope_name
    access_notice._connected_scope_name = lambda *a, **k: None
    try:
        message = access_notice.restricted_notice("google", org_id="o", workspace_id=None)
    finally:
        access_notice._connected_scope_name = original
    assert "an admin" in message


class _Store:
    """Minimal store: only what `_withheld_notice` touches."""

    def __init__(self, match):
        self._match = match

    def restricted_match(self, *a, **k):
        return self._match


def _pipeline_with(store):
    from app.rag.pipeline import RagPipeline

    pipeline = RagPipeline.__new__(RagPipeline)
    pipeline._store = store
    pipeline._source_provider = None

    class _S:
        similarity_threshold = 0.35

    pipeline._settings = _S()
    return pipeline


def test_nothing_withheld_keeps_the_ordinary_refusal():
    """The claim that must never be invented: a question nobody's documents
    answer has to stay "I don't know", or we send people to ask an admin for
    access to a document that does not exist."""
    pipeline = _pipeline_with(_Store(None))
    assert (
        pipeline._withheld_notice(
            "o", [0.1], workspace_id=None, viewer=Viewer(email="a@x.com")
        )
        is None
    )


def test_a_failing_probe_costs_the_wording_not_the_refusal():
    class _Boom:
        def restricted_match(self, *a, **k):
            raise RuntimeError("db down")

    pipeline = _pipeline_with(_Boom())
    assert (
        pipeline._withheld_notice(
            "o", [0.1], workspace_id=None, viewer=Viewer(email="a@x.com")
        )
        is None
    )


# -- the live SQL path ------------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="needs a database"
)


@pytestmark_db
def test_hybrid_retrieval_hides_the_same_document_on_both_legs():
    """A document withheld from the vector leg must not arrive through BM25 --
    the keyword filter lives inside the candidate CTE, before BM25 re-ranks in
    Python, and a regression there is invisible from the vector side alone.

    Uses a synthetic unit vector rather than the real embedder: this asserts on
    the WHERE clause, and loading BGE-M3 to test SQL costs 325MB and proves
    nothing extra (CLAUDE.md §5).
    """
    import uuid

    from app.db.connection import get_connection
    from app.vectorstore.pgvector_store import PgVectorStore

    store = PgVectorStore()
    dim = int(os.getenv("EMBEDDING_DIM", "1024"))
    vector = [0.0] * dim
    vector[0] = 1.0

    org_id = store.create_organization(f"acl-test-{uuid.uuid4().hex[:8]}")
    try:
        store.upsert_source_document(
            org_id,
            provider="google",
            external_id="private-1",
            title="Leave policy",
            chunks=["The parental leave allowance is twenty six weeks."],
            embeddings=[vector],
            is_public=False,
            viewers=["Owner@Example.com"],
        )
        store.upsert_source_document(
            org_id,
            provider="google",
            external_id="shared-1",
            title="Office address",
            chunks=["The parental leave desk is on the second floor."],
            embeddings=[vector],
            is_public=True,
        )

        outsider = Viewer(email="other@example.com")
        insider = Viewer(email="owner@example.com")

        # The outsider sees ONLY the scope-public document, on both legs.
        seen = {h.document_title for h in store.query(org_id, vector, viewer=outsider)}
        assert seen == {"Office address"}
        seen_kw = {
            h.document_title
            for h in store.keyword_search(org_id, "parental leave", vector, viewer=outsider)
        }
        assert seen_kw == {"Office address"}

        # The person it was shared with sees both. Case is normalized on both
        # sides, so a capitalised grant still matches a lowercase login.
        assert len(store.query(org_id, vector, viewer=insider)) == 2
        # No viewer at all is still unrestricted (ingest, eval, CLI).
        assert len(store.query(org_id, vector, viewer=None)) == 2

        # The refusal path can tell the outsider something was withheld, and
        # must NOT tell the insider that.
        match = store.restricted_match(org_id, vector, viewer=outsider, min_score=0.35)
        assert match is not None and match.source_provider == "google"
        assert store.restricted_match(org_id, vector, viewer=insider, min_score=0.35) is None

        # Revocation: replacing the access set takes effect immediately.
        store.set_source_document_access(
            org_id,
            provider="google",
            entries=[("private-1", False, ["someone-else@example.com"])],
        )
        assert len(store.query(org_id, vector, viewer=insider)) == 1
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))


# -- the two meanings of "no email" must not share a value -----------------


def test_public_only_is_not_the_same_as_unrestricted():
    """The distinction this feature lives or dies on. `Viewer(email=None)` is an
    internal caller with no filter; `public_only` is a person who may read only
    what the whole scope may read. Collapsing them turns an identity failure
    (or a Slack channel reply) into a leak."""
    assert Viewer.unrestricted().is_unrestricted
    assert not Viewer.public_only_viewer().is_unrestricted

    sql, params = _viewer_clause(Viewer.public_only_viewer())
    assert "doc_is_public" in sql
    # An empty ACL array: `doc_viewers && '{}'` is false for every row, so the
    # predicate reduces to "scope-public only".
    assert params == [[]]


def test_an_unresolvable_signed_in_identity_fails_closed():
    """`viewer_for` must never hand back an unrestricted viewer for a session."""
    from app.api import deps

    class _Session:
        user_id = "nobody"

    original = deps.get_user
    deps.get_user = lambda _uid: None
    try:
        viewer = deps.viewer_for(_Session())
    finally:
        deps.get_user = original
    assert not viewer.is_unrestricted


def test_no_session_at_all_is_unrestricted():
    """Internal, CLI and evaluation callers keep reading everything."""
    from app.api import deps

    assert deps.viewer_for(None).is_unrestricted


@pytestmark_db
def test_a_starter_chip_never_names_a_document_you_cannot_open():
    """A chip is a document TITLE. Offering "What does Q3 Redundancies say?" to
    someone who cannot open that file discloses the one thing the filter exists
    to withhold — and the chip would then answer with a refusal, which reads as
    a broken product on top of the leak."""
    import uuid

    from app.api.chat import _titles_for_scope_by_provider
    from app.db.connection import get_connection
    from app.vectorstore.pgvector_store import PgVectorStore

    store = PgVectorStore()
    dim = int(os.getenv("EMBEDDING_DIM", "1024"))
    vector = [0.0] * dim
    vector[0] = 1.0

    org_id = store.create_organization(f"acl-chip-{uuid.uuid4().hex[:8]}")
    try:
        store.upsert_source_document(
            org_id,
            provider="google",
            external_id="chip-private",
            title="Q3 Redundancies",
            chunks=["restricted"],
            embeddings=[vector],
            is_public=False,
            viewers=["owner@example.com"],
        )
        store.upsert_source_document(
            org_id,
            provider="google",
            external_id="chip-public",
            title="Office address",
            chunks=["public"],
            embeddings=[vector],
            is_public=True,
        )

        outsider = _titles_for_scope_by_provider(
            org_id, None, "google", Viewer(email="other@example.com").acl()
        )
        assert outsider == ["Office address"]

        insider = _titles_for_scope_by_provider(
            org_id, None, "google", Viewer(email="owner@example.com").acl()
        )
        assert set(insider) == {"Office address", "Q3 Redundancies"}
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))


# -- a skipped file is SAID, not just logged -------------------------------


def test_a_file_whose_sharing_is_unreadable_is_counted_apart_from_an_empty_one():
    """Two different failures with two different fixes. `documents_skipped`
    means "nothing in it"; this means "we are not allowed to know who may read
    it", and only the second sends someone to change a Drive permission."""
    from app.ingestion.pipeline import IngestResult

    result = IngestResult()
    assert result.documents_permission_unreadable == 0
    assert IngestResult(documents_skipped=3).documents_permission_unreadable == 0


def test_an_indexed_document_whose_sharing_goes_dark_is_frozen_and_counted():
    """FREEZE, not lock down: it keeps the viewers it already had, so a
    transient Drive error cannot silently empty a corpus. The count is what
    stops that freeze being invisible."""
    from app.ingestion.pipeline import _restamp_unchanged_access

    store = _RecordingStore()
    refs = [
        SourceRef(external_id="ok", title="A", access=DocAccess.restricted(["a@x.com"])),
        SourceRef(external_id="dark", title="B"),  # sharing no longer reported
    ]
    frozen = _restamp_unchanged_access(
        store, refs, set(), org_id="o", provider="google", workspace_id=None
    )
    assert frozen == 1
    # The readable one is still refreshed; the dark one is simply left alone,
    # never rewritten to "nobody".
    assert store.calls[0]["entries"] == [("ok", False, ["a@x.com"])]


# -- the person picking the folder is told, then and there ------------------


def _report(**over):
    """Run the real message builder over a stubbed Drive preflight."""
    from app.api import connection_ops

    base = {"checked": 5, "unreadable": 2, "files": ["Q3 Planning"], "truncated": False, "failed": False}
    base.update(over)
    original = connection_ops.preflight_folder_sharing
    connection_ops.preflight_folder_sharing = lambda t, f: base
    try:
        return connection_ops.drive_sharing_report("tok", "folder")
    finally:
        connection_ops.preflight_folder_sharing = original


def test_a_healthy_folder_says_nothing():
    """A warning box that appears on a folder with no problem teaches people to
    dismiss the box."""
    assert _report(unreadable=0, files=[]) is None


def test_a_preflight_that_could_not_run_says_nothing():
    """Never claim files are unindexable on the strength of a failed check."""
    assert _report(failed=True) is None


def test_the_warning_states_the_consequence_and_the_fix():
    report = _report()
    assert report is not None
    assert "Only you will see" in report["title"]
    # The consequence, in the reader's terms: the document IS added, for one
    # person, and everybody else is told so rather than left guessing.
    assert "connected Google account only" in report["detail"]
    assert "isn\u2019t shared with them" in report["detail"]
    assert "owner or editor" in report["fix"]          # the one thing to do
    assert report["count"] == 2 and report["checked"] == 5


def test_a_partial_check_admits_it_is_partial():
    """One `files.list` page, so a big or nested folder may hold more. Saying
    "2 files" when we only looked at the top is a number that reads as total."""
    assert "may be more" in _report(truncated=True)["detail"]
    assert "may be more" not in _report(truncated=False)["detail"]


def test_the_warning_names_the_files():
    """Unlike the notification we replaced, this one CAN name them: whoever is
    choosing the folder can already open it in Drive, so listing what is in it
    discloses nothing they do not have in front of them."""
    assert _report(files=["Q3 Planning", "Budget"])["files"] == ["Q3 Planning", "Budget"]


def test_the_preflight_never_raises_on_a_dead_drive():
    """It runs inside the save that the folder depends on: a diagnostic that
    can fail the save it precedes is an outage, not a diagnostic."""
    import httpx

    from app.sources import google_drive_utils

    original = httpx.get
    httpx.get = lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("down"))
    try:
        out = google_drive_utils.preflight_folder_sharing("tok", "folder")
    finally:
        httpx.get = original
    assert out["failed"] is True and out["unreadable"] == 0


def test_the_preflight_ignores_subfolders_but_admits_it_stopped_at_them():
    """A subfolder is not a document. The files inside it are, and they are a
    deeper walk than a check someone is waiting on should make."""
    import httpx

    from app.sources import google_drive_utils

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "files": [
                    {"name": "Sub", "mimeType": "application/vnd.google-apps.folder"},
                    {"name": "Readable", "permissions": [{"type": "user"}]},
                    {"name": "Hidden"},
                ]
            }

    original = httpx.get
    httpx.get = lambda *a, **k: _Resp()
    try:
        out = google_drive_utils.preflight_folder_sharing("tok", "folder")
    finally:
        httpx.get = original
    assert out["checked"] == 2 and out["unreadable"] == 1
    assert out["files"] == ["Hidden"]
    assert out["truncated"] is True  # a subfolder was not looked into


# -- Google Group grants ----------------------------------------------------
#
# Drive reports a group share as ONE opaque `group:<address>` grant. These pin
# the two halves that make it mean something: the entry's spelling agrees with
# what the adapter writes, and it is expanded from the ASKER's memberships so
# the stored row never has to be re-written when the group's membership moves.


def test_a_group_grant_is_stored_as_the_group_not_its_members():
    """The whole reason expansion is a read-side concern. Storing the members
    would make every group edit a re-stamp of every document it touches -- and
    a group edit moves no Drive metadata, so nothing would signal one is due."""
    from app.sources.google_drive import _file_access

    access = _file_access(
        {"permissions": [{"type": "group", "emailAddress": "Engineering@Corp.com"}]}
    )
    assert access is not None
    assert access.is_public is False
    assert access.viewers == ("group:engineering@corp.com",)


def test_the_two_sides_spell_a_group_entry_identically():
    """The same contract `test_read_and_write_spell_an_entry_the_same_way` pins
    for people. A mis-spelled group entry fails closed, which is safe and also
    completely silent -- so it is pinned rather than noticed."""
    from app.sources.google_drive import _file_access

    written = _file_access(
        {"permissions": [{"type": "group", "emailAddress": "eng@corp.com"}]}
    )
    read = Viewer(email="ada@corp.com", groups=("eng@corp.com",)).acl()
    assert written is not None
    assert written.viewers[0] in read


def test_a_member_of_the_group_satisfies_the_grant():
    assert Viewer(email="ada@corp.com", groups=("eng@corp.com",)).acl() == [
        "ada@corp.com",
        "domain:corp.com",
        "group:eng@corp.com",
    ]


def test_group_entries_are_lowercased_and_deduplicated():
    """A directory that lists the same group twice, or in a different case,
    must not change what the query means."""
    acl = Viewer(
        email="ada@corp.com", groups=("Eng@Corp.com", "eng@corp.com", "  ", "ops@corp.com")
    ).acl()
    assert acl.count("group:eng@corp.com") == 1
    assert "group:ops@corp.com" in acl
    assert all(entry.strip() == entry for entry in acl)


def test_groups_never_widen_a_public_only_viewer():
    """`public_only` is the fail-closed state an unresolvable identity gets.
    Handing it memberships must not turn it into a viewer of private documents
    -- the empty ACL is what makes `doc_viewers && '{}'` false for every row."""
    viewer = Viewer(email=None, public_only=True, groups=("eng@corp.com",))
    assert viewer.acl() == []
    assert _viewer_clause(viewer)[1] == [[]]


def test_group_expansion_is_off_unless_the_flag_is_on(monkeypatch):
    """It needs an extra OAuth scope, so switching it on forces every tenant to
    reconnect Google. Off, a group-shared document stays withheld -- which is
    the behaviour that shipped, not a regression."""
    from app.sources import google_groups

    google_groups.clear_cache()
    monkeypatch.delenv("GOOGLE_GROUPS_ENABLED", raising=False)

    def _boom(*a, **k):  # the directory must not even be reached
        raise AssertionError("looked up groups while the flag was off")

    monkeypatch.setattr(google_groups, "_fetch_groups", _boom)
    assert google_groups.groups_for("org", "ada@corp.com") == ()


def test_a_directory_failure_is_not_cached_as_an_answer(monkeypatch):
    """Caching "no groups" for the TTL because the directory blipped would lock
    someone out of every group-shared document for that window."""
    from app.sources import google_groups

    google_groups.clear_cache()
    monkeypatch.setenv("GOOGLE_GROUPS_ENABLED", "true")
    monkeypatch.setattr(
        "app.auth.credentials.get_live_connection_token", lambda *a, **k: "tok"
    )

    calls: list[str] = []

    def _fetch(token, email, **kwargs):
        calls.append(email)
        return None if len(calls) == 1 else ("eng@corp.com",)

    monkeypatch.setattr(google_groups, "_fetch_groups", _fetch)

    assert google_groups.groups_for("org", "ada@corp.com") == ()
    assert google_groups.groups_for("org", "ada@corp.com") == ("eng@corp.com",)
    # ...and the success IS cached, so a chat does not spend a call per question.
    assert google_groups.groups_for("org", "ada@corp.com") == ("eng@corp.com",)
    assert len(calls) == 2
    google_groups.clear_cache()


def test_memberships_are_cached_per_org(monkeypatch):
    """The token that answered belongs to one org's connection, so one tenant's
    directory must never answer for another's."""
    from app.sources import google_groups

    google_groups.clear_cache()
    monkeypatch.setenv("GOOGLE_GROUPS_ENABLED", "true")
    monkeypatch.setattr(
        "app.auth.credentials.get_live_connection_token", lambda *a, **k: "tok"
    )
    seen: list[tuple[str, str]] = []

    def _fetch(token, email, **kwargs):
        seen.append((token, email))
        return ("eng@corp.com",)

    monkeypatch.setattr(google_groups, "_fetch_groups", _fetch)
    google_groups.groups_for("org-a", "ada@corp.com")
    google_groups.groups_for("org-b", "ada@corp.com")
    google_groups.groups_for("org-a", "ada@corp.com")
    assert len(seen) == 2  # once per org, then cached
    google_groups.clear_cache()


def test_no_google_connection_means_no_groups(monkeypatch):
    """Most orgs will never switch this on. An org with no Google connection at
    all must keep answering questions, not fail one over a directory it has no
    token for."""
    from app.core.exceptions import ConfigurationError
    from app.sources import google_groups

    google_groups.clear_cache()
    monkeypatch.setenv("GOOGLE_GROUPS_ENABLED", "true")

    def _no_token(*a, **k):
        raise ConfigurationError("not connected")

    monkeypatch.setattr("app.auth.credentials.get_live_connection_token", _no_token)
    assert google_groups.groups_for("org", "ada@corp.com") == ()


def test_a_non_admin_connection_reports_no_groups(monkeypatch):
    """The EXPECTED failure: the Directory API needs a Workspace admin, and most
    connected accounts are not one. It must degrade to today's behaviour."""
    import httpx

    from app.sources import google_groups

    class _Resp:
        status_code = 403

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    assert google_groups._fetch_groups("tok", "ada@corp.com") is None


def test_an_address_outside_the_directory_is_a_real_empty_answer(monkeypatch):
    """404 means this person genuinely has no Workspace groups (an external
    collaborator), which is different from "we could not find out" -- and only
    the first may be cached."""
    import httpx

    from app.sources import google_groups

    class _Resp:
        status_code = 404

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    assert google_groups._fetch_groups("tok", "outsider@other.com") == ()


def test_the_directory_walk_is_bounded(monkeypatch):
    """CLAUDE.md §2: bound every external walk. Stopping early can only DROP
    memberships, which locks someone out rather than letting them in."""
    import httpx

    from app.sources import google_groups

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"groups": [{"email": "eng@corp.com"}], "nextPageToken": "more"}

    pages = {"n": 0}

    def _get(*a, **k):
        pages["n"] += 1
        return _Resp()

    monkeypatch.setattr(httpx, "get", _get)
    assert google_groups._fetch_groups("tok", "ada@corp.com") == ("eng@corp.com",)
    assert pages["n"] == google_groups.MAX_PAGES


def test_an_identified_person_is_built_through_one_constructor(monkeypatch):
    """Every surface that answers AS someone -- the app, a Slack DM, a scheduled
    digest -- goes through `viewer_for_person`, so a surface added later cannot
    quietly ship without group expansion."""
    from app.sources import google_groups

    monkeypatch.setattr(google_groups, "groups_for", lambda *a: ("eng@corp.com",))
    viewer = google_groups.viewer_for_person("org", "Ada@Corp.com")
    assert viewer.groups == ("eng@corp.com",)
    assert "group:eng@corp.com" in viewer.acl()
    # No address at all still fails CLOSED, never to unrestricted.
    assert google_groups.viewer_for_person("org", None).acl() == []
    assert google_groups.viewer_for_person("org", "  ").is_unrestricted is False


def test_the_directory_call_names_the_domain(monkeypatch):
    """`groups.list` requires `domain` or `customer`, and `customer` may not be
    combined with `userKey` -- so `userKey` alone is not a valid request. Taken
    from the ASKER's address, so a secondary domain resolves to itself."""
    import httpx

    from app.sources import google_groups

    sent: dict = {}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"groups": [{"email": "eng@corp.com"}]}

    def _get(url, **kwargs):
        sent.update(kwargs.get("params") or {})
        return _Resp()

    monkeypatch.setattr(httpx, "get", _get)
    assert google_groups._fetch_groups("tok", "ada@eu.corp.com") == ("eng@corp.com",)
    assert sent["domain"] == "eu.corp.com"
    assert sent["userKey"] == "ada@eu.corp.com"
    assert "customer" not in sent  # combining it with userKey is an API error


def test_an_address_with_no_domain_asks_nothing(monkeypatch):
    import httpx

    from app.sources import google_groups

    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called"))
    )
    assert google_groups._fetch_groups("tok", "not-an-email") == ()


# -- unreadable sharing: Onyx's owner-only fallback -------------------------
#
# The first version of this feature SKIPPED a document whose sharing Drive
# would not report. Onyx indexes it for the one account that could see it
# (`ee/onyx/external_permissions/google_drive/doc_sync.py`, "falling back to
# granting access to retriever user"), which is strictly better: the person who
# connected the folder can use their own document, and -- because the row now
# EXISTS -- `restricted_match` can see it, so a colleague gets "not shared with
# you" instead of a bare "I don't know".


def test_unreadable_sharing_falls_back_to_the_connected_account():
    from app.sources.google_drive import _file_access

    access = _file_access({"name": "Q3"}, lambda: "admin@corp.com")
    assert access is not None
    assert access.is_public is False
    assert access.viewers == ("admin@corp.com",)
    assert access.unreadable is True


def test_the_fallback_grants_NOBODY_else_even_if_they_have_access():
    """Onyx says this in the same words: other people may genuinely be able to
    read the file and they are still not granted it. An under-shared document
    is a complaint; an over-shared one is a leak."""
    from app.sources.google_drive import _file_access

    access = _file_access({"name": "Q3"}, lambda: "admin@corp.com")
    assert access is not None and len(access.viewers) == 1
    assert Viewer(email="someone@corp.com").acl()[0] not in access.viewers


def test_with_no_account_to_fall_back_to_the_document_is_still_skipped():
    """The last resort has to stay. With nobody we can prove may read it, there
    is no one to index it for."""
    from app.sources.google_drive import _file_access

    assert _file_access({"name": "Q3"}, lambda: None) is None
    assert _file_access({"name": "Q3"}, None) is None
    assert _file_access({"name": "Q3"}) is None


def test_a_readable_file_is_never_marked_unreadable():
    """`unreadable` must not become a synonym for "restricted" -- a file shared
    with exactly one person is a healthy sync, not a reportable one."""
    from app.sources.google_drive import _file_access

    access = _file_access(
        {"permissions": [{"type": "user", "emailAddress": "ada@corp.com"}]},
        lambda: "admin@corp.com",
    )
    assert access is not None
    assert access.unreadable is False
    assert access.viewers == ("ada@corp.com",)


def test_the_fallback_is_indexed_and_counted_not_dropped():
    """The behaviour change that closes the hole: it reaches the store."""
    from app.ingestion.pipeline import _doc_access

    ref = SourceRef(external_id="x", title="Q3")
    doc = SourceDocument(
        external_id="x", title="Q3", content="c", access=DocAccess.owner_only("admin@corp.com")
    )
    resolved = _doc_access(doc, ref, "google")
    assert resolved is not None  # NOT skipped
    is_public, viewers, unreadable = resolved
    assert (is_public, viewers, unreadable) == (False, ["admin@corp.com"], True)


def test_a_provider_that_cannot_report_sharing_is_not_flagged_unreadable():
    """Notion has no per-page permission API at all. That is a known ceiling,
    not a broken sync, and it must not light up a warning on every card."""
    from app.ingestion.pipeline import _doc_access

    ref = SourceRef(external_id="x", title="Page")
    doc = SourceDocument(external_id="x", title="Page", content="c")
    assert _doc_access(doc, ref, "notion") == (True, None, False)


def test_the_connected_account_is_resolved_once_for_the_whole_walk(monkeypatch):
    """It is the same answer for every file, and the fallback path would
    otherwise spend an API call per unreadable document."""
    import httpx

    from app.sources.google_drive import GoogleDriveAdapter

    calls = {"n": 0}

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"user": {"emailAddress": "Admin@Corp.com"}}

    def _get(*a, **k):
        calls["n"] += 1
        return _Resp()

    monkeypatch.setattr(httpx, "get", _get)
    adapter = GoogleDriveAdapter("tok", "folder")
    assert adapter._account_email() == "admin@corp.com"
    assert adapter._account_email() == "admin@corp.com"
    assert calls["n"] == 1


def test_a_failed_account_lookup_degrades_to_the_skip(monkeypatch):
    """Never raises, and is not retried per file: without an account we fall
    all the way back to leaving the document out, which is what shipped."""
    import httpx

    from app.sources.google_drive import GoogleDriveAdapter

    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "get", _boom)
    adapter = GoogleDriveAdapter("tok", "folder")
    assert adapter._account_email() is None
    assert adapter._account_email() is None
    assert calls["n"] == 1


def test_the_preflight_no_longer_claims_files_are_left_out(monkeypatch):
    """The wording had to move with the behaviour: telling someone a document
    was left out when it is in fact indexed -- for one person -- sends them
    hunting for a file that is there and answering."""
    from app.api import connection_ops

    monkeypatch.setattr(
        connection_ops,
        "preflight_folder_sharing",
        lambda *a, **k: {
            "checked": 5, "unreadable": 2, "files": ["A", "B"],
            "truncated": False, "failed": False,
        },
    )
    report = connection_ops.drive_sharing_report("tok", "folder")
    assert report is not None
    blob = f"{report['title']} {report['detail']}".lower()
    assert "left" not in blob and "won't" not in blob and "won’t" not in blob
    assert "connected google account only" in blob


def test_an_indexed_document_is_FROZEN_not_narrowed_to_the_owner():
    """The owner-only fallback is right for a NEW document and wrong for one
    already in the index: applying it on a re-listing would narrow an
    established corpus to one person the moment a Workspace setting changed --
    the exact failure the freeze exists to prevent -- and would report as zero
    frozen documents while doing it."""
    from app.ingestion.pipeline import _restamp_unchanged_access

    class _Store:
        def __init__(self):
            self.written = []

        def set_source_document_access(self, org_id, *, provider, entries, workspace_id):
            self.written.extend(entries)
            return len(entries)

    store = _Store()
    refs = [
        SourceRef(external_id="known", title="A", access=DocAccess.restricted(["ada@x.com"])),
        SourceRef(external_id="dark", title="B", access=DocAccess.owner_only("admin@x.com")),
    ]
    frozen = _restamp_unchanged_access(
        store, refs, set(), org_id="o", provider="google", workspace_id=None
    )
    assert frozen == 1                                   # the dark one is counted
    assert [e[0] for e in store.written] == ["known"]    # and NOT rewritten


def test_an_edited_document_whose_sharing_went_dark_is_frozen_not_narrowed():
    """The other already-indexed path. A document EDITED in the window its
    sharing went dark must behave like one that was not touched: both are
    already in the index with a real access set, and the owner-only fallback is
    a guess. Writing the guess over either narrows an established corpus
    because a Workspace setting changed -- and the content update waits with
    it, because re-indexing a document whose audience we cannot determine is
    exactly the thing we are declining to do."""
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from app.ingestion.pipeline import ingest_source
    from app.vectorstore.base import StoredSourceDocument

    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new = datetime(2026, 6, 1, tzinfo=timezone.utc)
    dark = DocAccess.owner_only("admin@corp.com")
    upserts: list[str] = []

    class _Adapter:
        def list_documents(self):
            return [SourceRef("dark", "Edited", last_modified=new, access=dark)]

        def fetch_document(self, external_id):
            return SourceDocument("dark", "Edited", "new body", last_modified=new, access=dark)

        def get_last_modified(self, external_id):
            return new

    class _Store:
        def list_source_documents(self, org_id, provider, workspace_id=None):
            return [StoredSourceDocument("d1", "google", "dark", "Edited", None, old)]

        def upsert_source_document(self, org_id, **kw):
            upserts.append(kw["external_id"])
            return "d1"

        def acknowledge_source_document(self, org_id, **kw):
            return "d1"

        def set_source_document_access(self, org_id, **kw):
            raise AssertionError("a document handled this run must not be re-stamped")

        def delete_source_documents(self, org_id, provider, external_ids, workspace_id=None):
            return 0

    result = ingest_source(
        _Adapter(),
        str(uuid.uuid4()),
        provider="google",
        embedder=SimpleNamespace(embed_documents=lambda texts: [[0.1]] * len(texts)),
        store=_Store(),
        contextual=SimpleNamespace(enabled=False),
    )
    assert upserts == []                               # the good ACL survives
    assert result.documents_permission_unreadable == 1  # and it is reported
    assert result.documents_ingested == 0


def test_a_readable_folder_never_looks_up_the_connected_account():
    """The fallback's cost must land only on the case that needs it. A healthy
    folder is the normal one, and `test_google_drive_source.py` counts the calls
    a listing makes precisely so an extra per-sync request cannot creep in
    unnoticed (the Slack `users.info` lesson, CLAUDE.md §5)."""
    from app.sources.google_drive import _file_access

    def _must_not_run():
        raise AssertionError("resolved the account for a file whose sharing we could read")

    access = _file_access(
        {"permissions": [{"type": "anyone"}]}, _must_not_run
    )
    assert access is not None and access.is_public is True
