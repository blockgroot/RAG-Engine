"""Second Brain 1.1: people, links and containers captured at sync.

Three things are pinned here, and each is a way the graph would silently go
wrong rather than fail loudly:

* an identity is keyed on a provider id or an email, NEVER a display name —
  two people called Priya must stay two people;
* capture costs ZERO extra API calls — each adapter's fake rejects any call
  it did not already make;
* the metadata survives every path that rewrites a document row, including
  deferred enrichment, which used to drop the row's access set too.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from app.config.settings import ContextualSettings
from app.ingestion.pipeline import (
    enrich_source_contextual,
    ingest_source,
    refresh_missing_meta,
)
from app.insights.github_facts import _commit_rows, _pull_rows, actor_key
from app.sources.base import DocAccess, SourceDocument, SourceRef
from app.sources.meta import (
    MAX_PEOPLE,
    build_meta,
    editor_key,
    extract_links,
    person,
)


# -- identity keys -----------------------------------------------------------


def test_a_name_alone_is_never_an_identity():
    assert person("slack", role="author", name="Priya") is None


def test_a_provider_id_wins_over_an_email():
    entry = person("slack", role="author", external_id="U1", email="Ada@X.com", name="Ada")
    assert entry["key"] == "slack:U1"
    assert entry["email"] == "ada@x.com"


def test_an_email_keys_when_there_is_no_id():
    assert person("google", role="editor", email=" Ada@X.com ")["key"] == "email:ada@x.com"


def test_two_people_with_one_name_stay_two_people():
    meta = build_meta(
        people=[
            person("slack", role="participant", external_id="U1", name="Priya"),
            person("slack", role="participant", external_id="U2", name="Priya"),
        ]
    )
    assert [p["key"] for p in meta["people"]] == ["slack:U1", "slack:U2"]


def test_editor_key_follows_the_editor_role_order():
    meta = build_meta(
        people=[
            person("linear", role="creator", external_id="c"),
            person("linear", role="assignee", external_id="a"),
        ]
    )
    assert editor_key(meta) == "linear:a"
    assert editor_key(None) is None


def test_a_cut_list_says_it_was_cut():
    meta = build_meta(
        people=[person("slack", role="mentioned", external_id=f"U{i}") for i in range(MAX_PEOPLE + 5)]
    )
    assert len(meta["people"]) == MAX_PEOPLE
    assert meta["truncated"] is True


def test_nothing_captured_is_none():
    assert build_meta(people=[None], links=[], containers=[None]) is None


# -- links in text -----------------------------------------------------------


def test_links_resolve_to_the_ids_each_adapter_stores():
    text = (
        "See https://linear.app/acme/issue/eng-142/fix-login and "
        "<https://github.com/Acme/API/pull/7|the PR>, the spec at "
        "https://www.notion.so/acme/Spec-0123456789abcdef0123456789abcdef, "
        "https://docs.google.com/document/d/1AbCdEfGhIjKlMn/edit and "
        "https://acme.slack.com/archives/C123/p1700000000123456 plus "
        "https://example.com/unrelated."
    )
    got = {(l["provider"], l["external_id"]) for l in extract_links(text)}
    assert got == {
        ("linear", "ENG-142"),
        ("github", "pr:acme/api#7"),
        ("notion", "01234567-89ab-cdef-0123-456789abcdef"),
        ("google", "1AbCdEfGhIjKlMn"),
        ("slack", "C123:1700000000.123456"),
    }


def test_a_slack_reply_permalink_points_at_its_thread():
    [found] = extract_links(
        "https://acme.slack.com/archives/C1/p1700000999000001?thread_ts=1700000000.000100&cid=C1"
    )
    assert found["external_id"] == "C1:1700000000.000100"


def test_the_same_link_twice_is_one_link():
    assert len(extract_links("https://linear.app/a/issue/ENG-1 https://linear.app/a/issue/ENG-1/x")) == 1


# -- adapters: capture with no extra calls ------------------------------------


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.headers = {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_slack_captures_authors_participants_and_mentions_without_new_calls(monkeypatch):
    from app.sources import SlackAdapter

    calls: list[str] = []

    def fake_get(url, *, params=None, headers=None, timeout=None):
        calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("conversations.replies"):
            return _Resp(
                {
                    "ok": True,
                    "messages": [
                        {"ts": "100.0", "user": "U1", "text": "Ping <@U9|zed> about <#C7|eng>"},
                        {"ts": "101.0", "user": "U2", "text": "on it"},
                    ],
                }
            )
        if url.endswith("users.info"):
            uid = params["user"]
            return _Resp(
                {"ok": True, "user": {"real_name": uid.lower(), "profile": {"email": f"{uid}@x.com"}}}
            )
        raise AssertionError(f"unexpected call to {url}")

    monkeypatch.setattr("app.sources.slack.httpx.get", fake_get)
    doc = SlackAdapter(token="t", channel_ids=["C1"]).fetch_document("C1:100.0")

    # Exactly the calls the thread body already needed: one replies, one
    # users.info per AUTHOR. The mentioned user is never looked up.
    assert sorted(calls) == ["conversations.replies", "users.info", "users.info"]
    people = {(p["key"], p["role"]) for p in doc.meta["people"]}
    assert ("slack:U1", "author") in people
    assert ("slack:U2", "participant") in people
    assert ("slack:U9", "mentioned") in people
    author = next(p for p in doc.meta["people"] if p["role"] == "author")
    assert author["email"] == "u1@x.com"
    assert {"provider": "slack", "external_id": "channel:C7"} in doc.meta["links"]
    assert doc.meta["containers"][0]["external_id"] == "C1"
    assert editor_key(doc.meta) == "slack:U1"


def test_drive_asks_for_editor_identity_and_parents_in_the_same_request():
    from app.sources.google_drive import _EDITOR_FIELDS, _file_meta

    assert "emailAddress" in _EDITOR_FIELDS and "permissionId" in _EDITOR_FIELDS
    meta = _file_meta(
        {
            "lastModifyingUser": {"displayName": "Ada", "emailAddress": "ada@x.com", "permissionId": "p1"},
            "parents": ["F1"],
        }
    )
    assert meta["people"][0]["key"] == "google:p1"
    assert meta["containers"] == [{"provider": "google", "kind": "folder", "external_id": "F1"}]


def test_linear_reads_people_team_and_attached_prs_from_the_one_issue_query(monkeypatch):
    from app.sources.linear import LinearAdapter

    queries: list[str] = []

    def fake_post(url, json, headers, timeout):
        queries.append(json["query"])
        return _Resp(
            {
                "data": {
                    "issue": {
                        "id": "i1",
                        "identifier": "ENG-1",
                        "title": "Fix",
                        "url": "https://linear.app/a/issue/ENG-1",
                        "updatedAt": "2026-09-01T00:00:00Z",
                        "description": "",
                        "assignee": {"id": "a1", "name": "Ada", "email": "ada@x.com"},
                        "creator": {"id": "c1", "name": "Cy", "email": None},
                        "team": {"id": "t1", "key": "ENG", "name": "Engineering"},
                        "labels": {"nodes": []},
                        "attachments": {"nodes": [{"url": "https://github.com/acme/api/pull/9"}]},
                        "comments": {"nodes": [{"body": "hi", "user": {"id": "u3", "name": "Bo"}}]},
                    }
                }
            }
        )

    monkeypatch.setattr("app.sources.linear.httpx.post", fake_post)
    doc = LinearAdapter(token="k").fetch_document("i1")

    assert len(queries) == 1
    roles = {(p["key"], p["role"]) for p in doc.meta["people"]}
    assert roles == {("linear:a1", "assignee"), ("linear:c1", "creator"), ("linear:u3", "commenter")}
    assert doc.meta["links"] == [
        {"provider": "github", "external_id": "pr:acme/api#9", "url": "https://github.com/acme/api/pull/9"}
    ]
    assert doc.meta["containers"][0]["name"] == "Engineering"


def test_notion_collects_mentions_during_the_block_walk_it_already_does():
    from app.sources.notion import NotionAdapter

    class _Users:
        def __init__(self):
            self.calls = []

        def retrieve(self, user_id):
            self.calls.append(user_id)
            return {"name": "Ada", "person": {"email": "ada@x.com"}}

    class _Pages:
        def retrieve(self, page_id):
            return {
                "id": page_id,
                "url": "https://notion.so/p",
                "properties": {"t": {"type": "title", "title": [{"plain_text": "Spec"}]}},
                "last_edited_by": {"id": "ed1"},
                "created_by": {"id": "cr1"},
                "parent": {"type": "database_id", "database_id": "db1"},
            }

    class _Children:
        def list(self, block_id, start_cursor=None, **_):
            return {
                "results": [
                    {
                        "id": "b1",
                        "type": "paragraph",
                        "has_children": False,
                        "paragraph": {
                            "rich_text": [
                                {"type": "mention", "plain_text": "@Zed",
                                 "mention": {"type": "user", "user": {"id": "z1"}}},
                                {"type": "mention", "plain_text": "Other",
                                 "mention": {"type": "page", "page": {"id": "pg2"}}},
                                {"type": "text", "plain_text": "ticket",
                                 "href": "https://linear.app/a/issue/ENG-5"},
                            ]
                        },
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            }

    class _Blocks:
        children = _Children()

    class _Client:
        users = _Users()
        pages = _Pages()
        blocks = _Blocks()

    adapter = NotionAdapter.__new__(NotionAdapter)
    adapter._client = _Client()
    adapter._editor_names = {}
    adapter._editor_emails = {}
    doc = adapter.fetch_document("page1")

    # One user lookup, for the EDITOR (as before); creator and mention cost nothing.
    assert _Client.users.calls == ["ed1"]
    roles = {(p["key"], p["role"]) for p in doc.meta["people"]}
    assert roles == {("notion:ed1", "editor"), ("notion:cr1", "creator"), ("notion:z1", "mentioned")}
    editor = next(p for p in doc.meta["people"] if p["role"] == "editor")
    assert editor["email"] == "ada@x.com"
    links = {(l["provider"], l["external_id"]) for l in doc.meta["links"]}
    assert links == {("notion", "pg2"), ("linear", "ENG-5")}
    assert doc.meta["containers"] == [{"provider": "notion", "kind": "database", "external_id": "db1"}]


# -- GitHub actor keys ---------------------------------------------------------


def test_actor_key_is_a_lowercased_login():
    assert actor_key("Ada") == "github:ada"
    assert actor_key(None) is None


def test_a_commit_matched_only_by_git_name_gets_no_actor_key():
    class _Commit:
        date = datetime(2026, 9, 1, tzinfo=timezone.utc)
        sha = "abc"
        repo = "acme/api"
        url = "u"
        author = "Sana Asiwal"  # the git display name fallback
        author_login = None

    [row] = _commit_rows("o", None, _Commit())
    assert row[4] == "Sana Asiwal"
    assert row[-1] is None


def test_pull_request_rows_carry_actor_keys():
    class _Pull:
        author = "Ada"
        merged_by = "Bo"
        repo = "acme/api"
        state = "merged"
        number = 1
        url = "u"
        created_at = merged_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
        lead_time_seconds = 1

    rows = _pull_rows("o", None, _Pull())
    assert [r[-1] for r in rows] == ["github:ada", "github:bo"]


# -- pipeline -----------------------------------------------------------------


class _Adapter:
    def __init__(self, docs: dict[str, SourceDocument]):
        self.docs = docs
        self.fetched: list[str] = []

    def list_documents(self):
        return [SourceRef(external_id=k, title=d.title, last_modified=d.last_modified) for k, d in self.docs.items()]

    def fetch_document(self, external_id):
        self.fetched.append(external_id)
        return self.docs[external_id]


class _Store:
    def __init__(self, missing: list[str] | None = None):
        self.upserts: list[dict] = []
        self.acks: list[dict] = []
        self.meta_writes: list[tuple] = []
        self.missing = list(missing or [])

    def list_source_documents(self, *a, **k):
        return []

    def delete_source_documents(self, *a, **k):
        return 0

    def set_source_document_access(self, *a, **k):
        return 0

    def upsert_source_document(self, org_id, **kw):
        self.upserts.append(kw)
        return "doc-id"

    def acknowledge_source_document(self, org_id, **kw):
        self.acks.append(kw)
        return "doc-id"

    def list_source_documents_missing_meta(self, org_id, *, provider, workspace_id=None, limit=25):
        return self.missing[:limit]

    def set_source_document_meta(self, org_id, *, provider, entries, workspace_id=None):
        self.meta_writes.extend(entries)
        return len(entries)


class _Embedder:
    def embed(self, texts):
        return [[0.0] for _ in texts]


def _doc(eid="d1", *, content="Body text long enough to chunk. See https://linear.app/a/issue/ENG-9", access=None, tags=None, editor=None):
    return SourceDocument(
        external_id=eid,
        title="T",
        content=content,
        last_modified=datetime(2026, 9, 1, tzinfo=timezone.utc),
        last_editor=editor,
        tags=tags,
        access=access,
        meta=build_meta(people=[person("slack", role="author", external_id="U1")]),
    )


@pytest.fixture(autouse=True)
def _no_first_sync_wait(monkeypatch):
    # A one-document first sync waits for the source's index to catch up;
    # irrelevant here and 50s per test.
    monkeypatch.setattr("app.ingestion.pipeline.time.sleep", lambda _s: None)


def test_ingest_stores_the_adapter_meta_plus_links_from_the_body(monkeypatch):
    monkeypatch.setenv("GRAPH_META_REFRESH_BATCH", "0")
    store = _Store()
    ingest_source(
        _Adapter({"d1": _doc()}), "org", provider="linear", embedder=_Embedder(), store=store,
        contextual=ContextualSettings(enabled=False),
    )
    [written] = store.upserts
    assert written["editor_key"] == "slack:U1"
    assert {"provider": "linear", "external_id": "ENG-9", "url": "https://linear.app/a/issue/ENG-9"} in written["source_meta"]["links"]


def test_a_document_with_nothing_to_capture_is_stored_as_empty_not_null(monkeypatch):
    monkeypatch.setenv("GRAPH_META_REFRESH_BATCH", "0")
    store = _Store()
    plain = SourceDocument(external_id="d1", title="T", content="Plain body text with no links at all.")
    ingest_source(
        _Adapter({"d1": plain}), "org", provider="notion", embedder=_Embedder(), store=store,
        contextual=ContextualSettings(enabled=False),
    )
    assert store.upserts[0]["source_meta"] == {}


def test_deferred_enrichment_keeps_access_tags_editor_and_meta():
    """Regression: enrichment REPLACES the row, and it used to pass only the run
    tags — every restricted Drive file came back scope-public after a sync."""
    store = _Store()
    restricted = _doc(
        access=DocAccess.restricted(["ada@x.com"]), tags=["slack-channel:C1"], editor="Ada"
    )

    class _LLM:
        def generate(self, prompt):
            return "Context."

    n = enrich_source_contextual(
        _Adapter({"d1": restricted}), "org", provider="google", external_ids=["d1"],
        embedder=_Embedder(), store=store, llm=_LLM(),
        contextual=ContextualSettings(enabled=True, defer=True, concurrency=1),
    )
    assert n == 1
    [written] = store.upserts
    assert written["is_public"] is False
    assert written["viewers"] == ["ada@x.com"]
    assert written["tags"] == ["slack-channel:C1"]
    assert written["last_editor"] == "Ada"
    assert written["editor_key"] == "slack:U1"


def test_enrichment_leaves_a_document_whose_sharing_it_cannot_read():
    store = _Store()
    no_access = _doc(access=None)  # google is ACL-capable: None means "unknown"

    class _LLM:
        def generate(self, prompt):
            return "Context."

    n = enrich_source_contextual(
        _Adapter({"d1": no_access}), "org", provider="google", external_ids=["d1"],
        embedder=_Embedder(), store=store, llm=_LLM(),
        contextual=ContextualSettings(enabled=True, defer=True, concurrency=1),
    )
    assert n == 0 and store.upserts == []


def test_meta_refresh_is_bounded_and_skips_documents_this_run_fetched():
    docs = {f"d{i}": _doc(f"d{i}") for i in range(5)}
    adapter = _Adapter(docs)
    store = _Store(missing=list(docs))
    n = refresh_missing_meta(adapter, store, org_id="o", provider="slack", skip_ids={"d0"}, batch=2)
    assert n == 2
    assert adapter.fetched == ["d1", "d2"]
    assert [e[0] for e in store.meta_writes] == ["d1", "d2"]
    assert store.meta_writes[0][2] == "slack:U1"


def test_meta_refresh_survives_a_document_the_source_now_refuses():
    class _Flaky(_Adapter):
        def fetch_document(self, external_id):
            if external_id == "bad":
                raise RuntimeError("gone")
            return super().fetch_document(external_id)

    store = _Store(missing=["bad", "d1"])
    n = refresh_missing_meta(_Flaky({"d1": _doc()}), store, org_id="o", provider="slack", batch=5)
    assert n == 1 and store.meta_writes[0][0] == "d1"


def test_meta_refresh_ignores_documents_gone_from_the_listing():
    store = _Store(missing=["gone", "d1"])
    adapter = _Adapter({"d1": _doc()})
    n = refresh_missing_meta(adapter, store, org_id="o", provider="slack", live_ids={"d1"}, batch=5)
    assert n == 1 and adapter.fetched == ["d1"]


def test_meta_refresh_off_at_zero():
    store = _Store(missing=["d1"])
    assert refresh_missing_meta(_Adapter({"d1": _doc()}), store, org_id="o", provider="slack", batch=0) == 0


# -- the live SQL path ---------------------------------------------------------


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs a database")
def test_meta_round_trips_through_postgres():
    from app.db.connection import get_connection
    from app.db.migrate import apply_schema
    from app.vectorstore.pgvector_store import PgVectorStore

    apply_schema()
    store = PgVectorStore()
    dim = int(os.getenv("EMBEDDING_DIM", "1024"))
    vector = [1.0] + [0.0] * (dim - 1)
    org_id = store.create_organization(f"meta-test-{uuid.uuid4().hex[:8]}")
    try:
        meta = build_meta(people=[person("slack", role="author", external_id="U1")])
        store.upsert_source_document(
            org_id, provider="slack", external_id="new", title="New",
            chunks=["c"], embeddings=[vector], source_meta=meta, editor_key="slack:U1",
        )
        # A row written before 1.1: no meta at all.
        store.upsert_source_document(
            org_id, provider="slack", external_id="old", title="Old", chunks=["c"], embeddings=[vector],
        )
        assert store.list_source_documents_missing_meta(org_id, provider="slack") == ["old"]

        store.set_source_document_meta(
            org_id, provider="slack", entries=[("old", {}, None)]
        )
        assert store.list_source_documents_missing_meta(org_id, provider="slack") == []

        with get_connection() as conn:
            rows = dict(
                conn.execute(
                    "SELECT source_external_id, source_editor_key FROM documents WHERE org_id = %s::uuid",
                    (org_id,),
                ).fetchall()
            )
            stored = conn.execute(
                "SELECT source_meta FROM documents WHERE org_id = %s::uuid AND source_external_id = 'new'",
                (org_id,),
            ).fetchone()[0]
        assert rows == {"new": "slack:U1", "old": None}
        assert stored["people"][0]["key"] == "slack:U1"
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))
