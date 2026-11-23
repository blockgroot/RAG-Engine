"""Reshaping the last answer ("shorter", "as a table") without searching.

The question check decides it (its quote must be in the question); the last
turn is reshaped only while its cited documents are still the asker's to read,
unchanged; the rewrite adds no link or citation the old answer lacked.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.insights import resolve
from app.memory.conversations import ConversationTurnRow
from app.rag import reshape as rs
from app.vectorstore.base import Viewer

DOC_A = "11111111-1111-1111-1111-111111111111"
DOC_B = "22222222-2222-2222-2222-222222222222"
CITED = [
    {"n": 1, "document_id": DOC_A, "title": "Leave policy", "provider": "notion",
     "url": "https://notion.so/leave"},
    {"n": 2, "document_id": DOC_B, "title": "Holidays", "provider": "google", "url": None},
]
ASKER = Viewer(email="a@example.com")


class _LLM:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def generate(self, prompt, max_tokens=None):
        self.prompts.append(prompt)
        return self.reply


def _turn(**kw):
    base = dict(turn_index=0, question="What is the leave policy?",
                answer="Employees get 25 days [1]. Holidays are listed separately [2].",
                created_at=datetime.now(timezone.utc), cited=list(CITED))
    base.update(kw)
    return ConversationTurnRow(**base)


# -- the question check -------------------------------------------------------


def test_reshape_is_kept_when_its_quote_is_in_the_question():
    llm = _LLM(json.dumps({"chart": None, "reshape": True,
                           "reshape_words": "make it shorter", "live": False}))
    intent = resolve.classify_route("make it shorter please", github=False, llm=llm,
                                    reshape=True)
    assert intent.reshape is True


def test_reshape_is_dropped_when_the_quote_is_not_in_the_question():
    llm = _LLM(json.dumps({"reshape": True, "reshape_words": "as a table", "live": False}))
    assert resolve.classify_route("who approves it?", github=False, llm=llm,
                                  reshape=True).reshape is False


def test_reshape_is_never_asked_or_kept_without_a_previous_answer():
    llm = _LLM(json.dumps({"reshape": True, "reshape_words": "shorter", "live": False}))
    assert resolve.classify_route("shorter", github=False, llm=llm).reshape is False
    assert "reshape" not in llm.prompts[0]  # first questions see the old prompt


def test_the_quote_is_read_from_the_last_line_of_a_follow_up():
    # With table answers on, the check reads "(earlier question...)\n<question>".
    llm = _LLM(json.dumps({"reshape": True, "reshape_words": "leave policy", "live": False}))
    checked = resolve.with_earlier("in Hindi", "What is the leave policy?")
    assert resolve.classify_route(checked, github=False, llm=llm, reshape=True).reshape is False


# -- routing --------------------------------------------------------------------


def test_routing_returns_reshape_only_when_allowed(monkeypatch):
    from app.agent import routing

    seen = []

    def classify(question, *, github, table=False, reshape=False, llm=None):
        seen.append(reshape)
        return resolve.AskIntent("qa", reshape=reshape)

    monkeypatch.setattr(resolve, "classify_route", classify)
    got = routing._try_question_route("shorter", set(), "org", None, context="q?", reshape=True)
    assert got is not None and got.reason == "reshape"
    assert routing._try_question_route("shorter", set(), "org", None, context="q?") is None
    assert seen == [True, False]


def test_a_chart_ask_is_not_a_reshape(monkeypatch):
    from app.agent import routing

    monkeypatch.setattr(resolve, "classify_route", lambda *a, **k: resolve.AskIntent(
        "qa", reshape=True, chart_ask="visual"))
    got = routing._try_question_route("as a chart", set(), "org", None, context="q?",
                                      reshape=True)
    assert got.reason != "reshape"


# -- may the last turn be reshaped? -------------------------------------------


def test_reshapeable_needs_every_cited_document_readable_and_unchanged(monkeypatch):
    calls = []

    def readable(ids, **kw):
        calls.append(kw)
        return set(ids)

    monkeypatch.setattr(rs, "readable_unchanged", readable)
    turn = _turn()
    assert rs.reshapeable(turn, org_id="o", workspace_id="w", viewer=ASKER) is True
    assert calls[0]["viewer"] == ASKER and calls[0]["workspace_id"] == "w"
    assert calls[0]["since"] == turn.created_at

    monkeypatch.setattr(rs, "readable_unchanged", lambda ids, **kw: {DOC_A})  # B revoked
    assert rs.reshapeable(turn, org_id="o", workspace_id=None, viewer=ASKER) is False


@pytest.mark.parametrize("turn", [
    None,
    _turn(cited=[]),                                    # a refusal or uncited answer
    _turn(chart={"title": "x"}),                        # a chart is not reshaped
    _turn(cited=[{"n": 1, "document_id": "gh:repo"}]),  # a live read, not a document
])
def test_reshapeable_is_false_for_turns_it_cannot_vouch_for(monkeypatch, turn):
    monkeypatch.setattr(rs, "readable_unchanged", lambda ids, **kw: set(ids))
    assert rs.reshapeable(turn, org_id="o", workspace_id=None, viewer=ASKER) is False


def test_reshapeable_needs_a_viewer_and_fails_closed(monkeypatch):
    monkeypatch.setattr(rs, "readable_unchanged", lambda ids, **kw: set(ids))
    assert rs.reshapeable(_turn(), org_id="o", workspace_id=None, viewer=None) is False

    def broken(ids, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(rs, "readable_unchanged", broken)
    assert rs.reshapeable(_turn(), org_id="o", workspace_id=None, viewer=ASKER) is False


# -- the rewrite ---------------------------------------------------------------


@pytest.fixture
def _safe(monkeypatch):
    monkeypatch.setattr("app.guard.moderation.answer_is_unsafe", lambda *a, **k: False)


def test_rewrite_keeps_old_citations_and_drops_invented_ones(_safe):
    llm = _LLM("25 days of leave [2]. See https://evil.example/x [7].")
    answer, cited = rs.reshape("shorter", _turn(), org_id="o", llm=llm)
    assert "evil.example" not in answer
    assert "[7]" not in answer
    # Old [2] is now the only document: renumbered to [1].
    assert "[1]" in answer and [c["document_id"] for c in cited] == [DOC_B]
    assert "UNTRUSTED_PREVIOUS_ANSWER" in llm.prompts[0]


def test_a_rewrite_with_no_marker_keeps_the_turns_sources(_safe):
    answer, cited = rs.reshape("as a table", _turn(), org_id="o",
                               llm=_LLM("| Leave | 25 days |"))
    assert cited == CITED


def test_a_failed_or_empty_rewrite_returns_none(_safe):
    class _Down:
        def generate(self, *a, **k):
            raise RuntimeError("429")

    assert rs.reshape("shorter", _turn(), org_id="o", llm=_Down()) is None
    assert rs.reshape("shorter", _turn(), org_id="o", llm=_LLM("  ")) is None


def test_an_unsafe_rewrite_returns_none(monkeypatch):
    monkeypatch.setattr("app.guard.moderation.answer_is_unsafe", lambda *a, **k: True)
    assert rs.reshape("shorter", _turn(), org_id="o", llm=_LLM("short [1]")) is None


# -- real SQL ------------------------------------------------------------------


def test_readable_unchanged_applies_access_scope_and_resync():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("needs a database")
    from app.db.connection import get_connection
    from app.vectorstore.pgvector_store import PgVectorStore

    store = PgVectorStore()
    vector = [0.0] * int(os.getenv("EMBEDDING_DIM", "1024"))
    vector[0] = 1.0
    org = store.create_organization(f"reshape-test-{uuid.uuid4().hex[:8]}")
    try:
        for ext, public in (("pub", True), ("priv", False)):
            store.upsert_source_document(
                org, provider="google", external_id=ext, title=ext, chunks=[f"{ext} text"],
                embeddings=[vector], is_public=public,
                viewers=None if public else ["owner@example.com"],
            )
        docs = {h.document_title: h.document_id for h in store.query(org, vector, top_k=10)}
        ids = set(docs.values())
        with get_connection() as conn:
            now = conn.execute("SELECT now()").fetchone()[0]
        later, earlier = now + timedelta(hours=1), now - timedelta(hours=1)

        def read(viewer, since, ws=None):
            return rs.readable_unchanged(ids, org_id=org, workspace_id=ws, viewer=viewer,
                                         since=since)

        assert read(Viewer(email="owner@example.com"), later) == ids
        assert read(Viewer(email="other@example.com"), later) == {docs["pub"]}
        assert read(Viewer(email="owner@example.com"), earlier) == set()  # re-synced since
        assert read(Viewer(email="owner@example.com"), later, ws=str(uuid.uuid4())) == set()
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org,))
