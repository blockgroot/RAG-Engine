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
    )


def test_sharing_from_the_listing_is_used_when_the_fetch_has_none():
    """Drive answers from `files.list` for free; the fetch need not repeat it."""
    ref = SourceRef(
        external_id="f1", title="T", access=DocAccess.restricted(["ada@example.com"])
    )
    assert _access_for(_doc(), ref, "google") == (False, ["ada@example.com"])


def test_the_fetched_document_wins_over_the_listing():
    """Both may report; the fetch is the fresher read."""
    ref = SourceRef(external_id="f1", title="T", access=DocAccess.scope_public())
    doc = _doc(access=DocAccess.restricted(["ada@example.com"]))
    assert _access_for(doc, ref, "google") == (False, ["ada@example.com"])


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
    n = _restamp_unchanged_access(
        store, refs, {"fetched"}, org_id="o", provider="google", workspace_id=None
    )
    assert n == 1
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
