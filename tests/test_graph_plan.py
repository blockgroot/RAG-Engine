"""The graph plan: one walk per question, shared by routing, retrieval and the answer.

Pinned here:

* normal answers stay inside their own tool -- documents AND facts;
* only a connected answer reads other tools, and only documents the graph proved;
* retrieval REUSES the plan (no second walk) and keeps its vector/keyword legs pinned;
* an exact identifier routes to its tool; two tools resolve to neither;
* graph-shaped answers are never written to the scope-wide cache;
* the chat edge escalates a refusal ONCE, replaces the refused turn, and is
  byte-identical with the flag off.
"""

from __future__ import annotations

import json
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from app.agent import routing
from app.config.settings import GraphSettings, RagSettings, RetrievalSettings
from app.graph import plan as gp
from app.graph.linking import Seed
from app.graph.walk import WalkedLink, WalkResult
from app.rag import pipeline as rag_pipeline
from app.rag.retrieval import HybridRetriever, _reserve_other_tools
from app.vectorstore.base import RetrievedChunk, Viewer

ORG = "11111111-1111-1111-1111-111111111111"


def _plan(cross=None, seeds=()):
    walk = WalkResult(
        document_ids=["n1", "n2", "s1"],
        edges=4,
        document_providers={"n1": "notion", "n2": "notion", "s1": "slack"},
        links=[
            WalkedLink("Sana", "identity:notion:u1", "authored", "Leave Policy", "notion:p1", 1),
            WalkedLink("Sana", "identity:notion:u1", "authored", "Travel Policy", "notion:p2", 1),
            WalkedLink("Sana", "identity:notion:u1", "same_person", "sana", "identity:slack:U1", 1),
            WalkedLink("sana", "identity:slack:U1", "authored", "#hr: leave question", "slack:C1:1", 2),
        ],
    )
    return gp.GraphPlan(ORG, None, tuple(seeds), walk, cross)


# --------------------------------------------------------------------------
# The plan itself
# --------------------------------------------------------------------------


def test_provider_of_reads_every_key_shape():
    assert gp.provider_of("identity:slack:U1") == "slack"
    assert gp.provider_of("notion:abc") == "notion"
    assert gp.provider_of("google:file") == "google"
    assert gp.provider_of("user:42") is None
    assert gp.provider_of("") is None
    assert gp.provider_of("mystery:1") is None


def test_a_normal_answer_sees_only_its_own_tool():
    plan = _plan()
    assert set(plan.documents_for("notion")) == {"n1", "n2"}
    facts = plan.facts("notion")
    assert facts == [
        "Sana (Notion) wrote Leave Policy (Notion)",
        "Sana (Notion) wrote Travel Policy (Notion)",
    ]
    # Never learns that a Slack thread exists, nor who the Slack account is.
    assert not any("Slack" in f for f in facts)


def test_a_connected_answer_crosses_only_to_the_tools_it_was_given():
    plan = _plan().connected({"notion", "slack"})
    assert set(plan.documents_for("notion")) == {"n1", "n2", "s1"}
    facts = plan.facts("notion")
    assert "Sana (Notion) is the same person as sana (Slack)" in facts
    assert "sana (Slack) wrote #hr: leave question (Slack)" in facts
    only_notion = _plan().connected({"notion", "linear"})
    assert "s1" not in only_notion.documents_for("notion")


def test_facts_are_bounded(monkeypatch):
    many = [
        WalkedLink(f"P{i}", "identity:notion:u", "authored", f"Doc {i}", f"notion:{i}", 1)
        for i in range(50)
    ]
    plan = gp.GraphPlan(ORG, None, (), WalkResult(document_ids=[], edges=50, links=many))
    facts = plan.facts("notion")
    assert len(facts) == gp.MAX_FACTS
    assert sum(len(f) for f in facts) <= gp.MAX_FACTS_CHARS


def test_three_or_more_of_one_kind_become_one_grouped_fact():
    links = [WalkedLink("Sana", "identity:slack:U1", "authored", f"#hr: thread {i}",
                        f"slack:C:{i}", 2) for i in range(6)]
    plan = gp.GraphPlan(ORG, None, (), WalkResult(document_ids=[], edges=6, links=links),
                        cross=frozenset({"slack"}))
    assert plan.facts("slack") == [
        'Sana (Slack) wrote 6 Slack items found, including: "#hr: thread 0"; '
        '"#hr: thread 1"; "#hr: thread 2"; "#hr: thread 3" and 2 more'
    ]


def test_build_plan_needs_the_flag_and_a_viewer(monkeypatch):
    on = GraphSettings(retrieval_enabled=True)
    off = GraphSettings(retrieval_enabled=False)
    monkeypatch.setattr("app.graph.linking.link_question", lambda *a, **k: 1 / 0)
    assert gp.build_plan(ORG, None, "q", Viewer(email="a@x.io"), settings=off) is None
    assert gp.build_plan(ORG, None, "q", None, settings=on) is None
    # A failure costs the plan, never the question.
    assert gp.build_plan(ORG, None, "q", Viewer(email="a@x.io"), settings=on) is None


def test_predictive_connection_needs_a_named_indexed_tool():
    plan = _plan()
    assert gp.connected_tools(plan, "notion", "slack") == {"notion", "slack"}
    # Naming the tool is enough: the asker said where to look.
    assert gp.connected_tools(plan, "notion", "linear") == {"notion", "linear"}
    assert gp.connected_tools(plan, "notion", "github") is None  # nothing indexed to search
    assert gp.connected_tools(plan, "notion", None) is None
    assert gp.connected_tools(plan, "notion", "notion") is None
    assert gp.connected_tools(plan, "github", "slack") is None  # no index to widen
    assert gp.connected_tools(None, "notion", "slack") is None


def test_escalation_only_when_other_tools_have_evidence():
    assert gp.escalation_tools(_plan(), "notion") == {"notion", "slack"}
    assert gp.escalation_tools(_plan(), "linear") == {"linear", "notion", "slack"}
    assert gp.escalation_tools(_plan().connected({"notion", "slack"}), "notion") is None
    lonely = gp.GraphPlan(ORG, None, (), WalkResult(
        document_ids=["n1"], edges=1, document_providers={"n1": "notion"}))
    assert gp.escalation_tools(lonely, "notion") is None


# --------------------------------------------------------------------------
# Retrieval reuses the plan
# --------------------------------------------------------------------------


class _Store:
    def __init__(self):
        self.calls: list[dict] = []

    def query(self, org_id, embedding, **kw):
        self.calls.append(kw)
        if kw.get("document_ids"):
            return [RetrievedChunk(content="s", score=0.4, document_id="s1", chunk_index=0, org_id=org_id,
                                   source_provider="slack")]
        return [RetrievedChunk(content="n", score=0.6, document_id="n1", chunk_index=0, org_id=org_id,
                               source_provider="notion")]

    def keyword_search(self, *a, **kw):
        return []


def _retriever(store):
    return HybridRetriever(
        store,
        reranker=None,
        settings=RetrievalSettings(hybrid_enabled=False, rerank_enabled=False),
        rag_settings=RagSettings(top_k=5),
        graph_settings=GraphSettings(retrieval_enabled=True),
        source_provider="notion",
    )


@pytest.fixture
def no_second_walk(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("retrieval walked again although a plan was active")

    monkeypatch.setattr("app.graph.linking.link_question", boom)
    monkeypatch.setattr("app.graph.walk.walk", boom)


def test_retrieval_reuses_a_normal_plan_and_stays_pinned(no_second_walk):
    store = _Store()
    token = gp.use_plan(_plan())
    try:
        _retriever(store).retrieve(ORG, "q", [1.0], viewer=Viewer(email="a@x.io"))
    finally:
        gp.reset_plan(token)
    graph = [c for c in store.calls if c.get("document_ids")]
    assert graph and set(graph[0]["document_ids"]) == {"n1", "n2"}
    assert all(c["source_provider"] == "notion" for c in store.calls)


def test_a_connected_plan_lifts_the_pin_on_the_graph_leg_only(no_second_walk):
    store = _Store()
    asker = Viewer(email="a@x.io")
    token = gp.use_plan(_plan().connected({"notion", "slack"}))
    try:
        result = _retriever(store).retrieve(ORG, "q", [1.0], viewer=asker)
    finally:
        gp.reset_plan(token)
    graph = [c for c in store.calls if c.get("document_ids")]
    others = [c for c in store.calls if not c.get("document_ids")]
    assert graph[0]["source_provider"] is None and graph[0]["viewer"] is asker
    assert set(graph[0]["document_ids"]) == {"n1", "n2", "s1"}
    assert all(c["source_provider"] == "notion" for c in others)
    assert {h.source_provider for h in result.hits} == {"notion", "slack"}
    assert result.gate_score == 0.6  # still the best cosine


def test_a_plan_for_another_scope_is_ignored(monkeypatch):
    walked = []
    monkeypatch.setattr("app.graph.linking.link_question", lambda *a, **k: walked.append(1) or [])
    token = gp.use_plan(gp.GraphPlan("someone-else", None))
    try:
        _retriever(_Store()).retrieve(ORG, "q", [1.0], viewer=Viewer(email="a@x.io"))
    finally:
        gp.reset_plan(token)
    assert walked == [1]


def test_a_named_tool_runs_its_own_searches_beside_the_routed_ones(monkeypatch):
    """No graph evidence at all, and Slack still answers -- because it was named."""
    monkeypatch.setattr("app.graph.linking.link_question", lambda *a, **k: [])

    class _ByTool(_Store):
        def query(self, org_id, embedding, **kw):
            self.calls.append(kw)
            tool = kw.get("source_provider")
            return [RetrievedChunk(content=tool, score=0.5 if tool == "slack" else 0.6,
                                   document_id=f"{tool}-doc", chunk_index=0, org_id=org_id,
                                   source_provider=tool)]

    store = _ByTool()
    asker = Viewer(email="a@x.io")
    plan = gp.GraphPlan(ORG, None).connected({"notion", "slack"}, search={"slack"})
    token = gp.use_plan(plan)
    try:
        result = _retriever(store).retrieve(ORG, "q", [1.0], viewer=asker)
    finally:
        gp.reset_plan(token)
    assert sorted(c["source_provider"] for c in store.calls) == ["notion", "slack"]
    assert all(c["viewer"] is asker for c in store.calls)
    assert {h.source_provider for h in result.hits} == {"notion", "slack"}


def test_a_normal_plan_runs_no_extra_search():
    store = _Store()
    token = gp.use_plan(gp.GraphPlan(ORG, None))
    try:
        _retriever(store).retrieve(ORG, "q", [1.0], viewer=Viewer(email="a@x.io"))
    finally:
        gp.reset_plan(token)
    assert [c["source_provider"] for c in store.calls] == ["notion"]


def _cand(edge, tool, score, frm="hub"):
    return {"edge_id": edge, "nx_id": edge, "key": f"{tool}:{edge}", "kind": "document",
            "name": edge, "from_id": frm, "relation": "authored", "score": score,
            "last_seen": None}


def test_fair_pick_rotates_across_tools_and_caps_a_hub():
    from app.graph.walk import _fair_pick

    many = [_cand(f"n{i}", "notion", 1.0) for i in range(30)]
    few = [_cand("s1", "slack", 0.1), _cand("s2", "slack", 0.0)]
    picked, left = _fair_pick(many + few, 6, per_node=20, focus=frozenset())
    tools = [c["key"].split(":")[0] for c in picked]
    assert tools.count("slack") == 2 and left  # Slack seated despite lower scores
    capped, left = _fair_pick(many, 50, per_node=5, focus=frozenset())
    assert len(capped) == 5 and left  # the hub cap is a bound, reported


def test_fair_pick_gives_a_named_tool_the_larger_share():
    from app.graph.walk import _fair_pick

    cands = [_cand(f"n{i}", "notion", 1.0) for i in range(10)] + \
            [_cand(f"s{i}", "slack", 0.5) for i in range(10)]
    picked, _ = _fair_pick(cands, 6, per_node=20, focus=frozenset({"slack"}))
    assert [c["key"].split(":")[0] for c in picked].count("slack") == 4


def test_fair_pick_prefers_the_best_match_within_a_tool():
    from app.graph.walk import _fair_pick

    picked, _ = _fair_pick([_cand("weak", "notion", 0.1), _cand("strong", "notion", 0.9)],
                           1, per_node=20, focus=frozenset())
    assert picked[0]["edge_id"] == "strong"


def test_question_tsquery_is_plain_words_joined_by_or():
    from app.graph.walk import question_tsquery

    assert question_tsquery("Has Sana discussed it in Slack?") == "has | sana | discussed | slack"
    assert question_tsquery("x'); DROP TABLE users; --") == "drop | table | users"
    assert question_tsquery("") is None


def _c(doc, tool):
    return RetrievedChunk(content=doc, score=0.5, document_id=doc, chunk_index=0, org_id=ORG,
                          source_provider=tool)


def test_reserved_slots_seat_other_tools_but_keep_the_routed_majority():
    ordered = [_c(f"n{i}", "notion") for i in range(8)] + [_c("s", "slack"), _c("l", "linear"),
                                                           _c("g", "google")]
    final = _reserve_other_tools(ordered, 5, "notion")
    assert len(final) == 5
    assert [c.document_id for c in final[:3]] == ["n0", "n1", "n2"]
    assert {c.source_provider for c in final} == {"notion", "slack", "linear"}  # max 2 swaps
    # Already present: nothing moves.
    mixed = [_c("n0", "notion"), _c("s", "slack")] + [_c(f"n{i}", "notion") for i in range(1, 6)]
    assert _reserve_other_tools(mixed, 5, "notion") == mixed[:5]


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def _routing_stub(monkeypatch, connected, scores):
    monkeypatch.setattr(routing, "_try_insights_route", lambda *a, **k: None)
    monkeypatch.setattr(routing, "_connected_providers", lambda *a, **k: connected)
    monkeypatch.setattr(routing, "_probe_scores", lambda *a, **k: scores)
    monkeypatch.setattr(routing, "_named_repo", lambda *a, **k: None)


def test_an_exact_identifier_routes_to_the_tool_that_holds_it(monkeypatch):
    _routing_stub(monkeypatch, {"notion", "linear"}, {"notion": 0.7, "linear": 0.5})
    plan = gp.GraphPlan(ORG, None, (Seed("e", "issue", "SYV-6", True, 1.0, "linear:SYV-6"),))
    decision = routing.choose_agent("who owns SYV-6?", ORG, graph_plan=plan)
    assert (decision.agent_key, decision.reason) == ("linear", "graph-named")


def test_a_future_plan_is_awaited_and_absence_changes_nothing(monkeypatch):
    _routing_stub(monkeypatch, {"notion", "linear"}, {"notion": 0.7, "linear": 0.5})
    fut: Future = Future()
    fut.set_result(gp.GraphPlan(ORG, None, (Seed("e", "issue", "SYV-6", True, 1.0, "linear:x"),)))
    assert routing.choose_agent("q", ORG, graph_plan=fut).agent_key == "linear"
    baseline = routing.choose_agent("q", ORG)
    without = routing.choose_agent("q", ORG, graph_plan=None)
    assert (without.agent_key, without.reason) == (baseline.agent_key, baseline.reason)
    assert baseline.agent_key == "notion"


def test_fuzzy_seeds_and_two_tools_resolve_to_the_probe(monkeypatch):
    _routing_stub(monkeypatch, {"notion", "linear", "slack"}, {"notion": 0.7})
    fuzzy = gp.GraphPlan(ORG, None, (Seed("e", "issue", "Token bug", False, 0.8, "linear:x"),))
    assert routing.choose_agent("q", ORG, graph_plan=fuzzy).agent_key == "notion"
    two = gp.GraphPlan(ORG, None, (Seed("a", "issue", "A-1", True, 1, "linear:a"),
                                   Seed("b", "pr", "#2", True, 1, "github:b")))
    assert routing.choose_agent("q", ORG, graph_plan=two).agent_key == "notion"
    failed: Future = Future()
    failed.set_exception(RuntimeError("down"))
    assert routing.choose_agent("q", ORG, graph_plan=failed).agent_key == "notion"


# --------------------------------------------------------------------------
# The answer: facts, cache
# --------------------------------------------------------------------------


def test_facts_block_reads_the_plan_and_marks_the_answer():
    token = gp.use_plan(_plan())
    try:
        block, shaped = rag_pipeline._graph_facts_block(ORG, "notion")
    finally:
        gp.reset_plan(token)
    assert shaped and block.startswith(rag_pipeline.GRAPH_FACTS_HEADER)
    assert "Travel Policy" in block and "Slack" not in block
    assert rag_pipeline._graph_facts_block(ORG, "notion") == (None, False)  # no plan


def test_a_connected_answer_is_told_what_was_searched():
    plan = gp.replace(_plan(), coverage=(("slack", 11),)).connected({"notion", "slack"},
                                                                    search={"slack"})
    token = gp.use_plan(plan)
    try:
        block, shaped = rag_pipeline._graph_facts_block(ORG, "notion")
    finally:
        gp.reset_plan(token)
    assert shaped
    assert rag_pipeline.SEARCH_COVERAGE_HEADER in block
    assert "Slack was searched for this question: 11 items" in block
    # A normal answer is never told about a search it did not run.
    token = gp.use_plan(gp.replace(_plan(), coverage=(("slack", 11),)))
    try:
        block, _ = rag_pipeline._graph_facts_block(ORG, "notion")
    finally:
        gp.reset_plan(token)
    assert rag_pipeline.SEARCH_COVERAGE_HEADER not in block


def test_graph_shaped_answers_are_never_cached():
    ok = rag_pipeline.RagResult(answer="a", answered=True)
    assert rag_pipeline._is_cacheable(ok)
    assert not rag_pipeline._is_cacheable(rag_pipeline.RagResult(
        answer="a", answered=True, graph_shaped=True))


# --------------------------------------------------------------------------
# The chat edge
# --------------------------------------------------------------------------


def _response(grounded, answer):
    return SimpleNamespace(
        answer=answer, grounded=grounded, source="policy", citations=[],
        resolved_question=None, latency_ms=1.0, access_restricted=False, top_score=0.5,
        chart=None, chart_period=None,
    )


@pytest.fixture
def chat_edge(monkeypatch):
    from app.api import chat

    seen = {"plans": [], "dropped": [], "gaps": []}
    responses: list = []

    class _Graph:
        def invoke(self, state):
            seen["plans"].append(gp.current_plan())
            return {"response": responses.pop(0)}

    monkeypatch.setattr(chat, "use_model", lambda *a, **k: None)
    monkeypatch.setattr(chat, "_conversation_attachments", lambda *a, **k: [])
    monkeypatch.setattr(chat, "_previous_question", lambda *a, **k: None)
    monkeypatch.setattr(chat, "_agent_graph", lambda: _Graph())
    monkeypatch.setattr(chat, "viewer_for", lambda s: Viewer(email="a@x.io"))
    monkeypatch.setattr(chat, "record_gap", lambda **k: seen["gaps"].append(k))
    monkeypatch.setattr(chat, "_stream_word_delay_seconds", lambda: 0)
    monkeypatch.setattr(chat, "choose_agent",
                        lambda *a, **k: routing.RoutingDecision("notion", "best-match"))
    monkeypatch.setattr(chat, "_drop_refusal_turn",
                        lambda *a: seen["dropped"].append(a))
    monkeypatch.setenv("GRAPH_CONNECTED_ENABLED", "true")
    monkeypatch.setattr(chat, "_connected_providers",
                        lambda *a, **k: {"notion", "slack", "linear", "google"})

    def run(question, plan):
        fut = None
        if plan is not None:
            fut = Future()
            fut.set_result(plan)
        monkeypatch.setattr(chat, "_start_graph_plan", lambda *a, **k: fut)
        out = list(chat._stream_answer(question, ORG, "conv-1"))
        done = json.loads(out[-1].split("data: ", 1)[1])
        return done

    return SimpleNamespace(run=run, seen=seen, responses=responses)


def test_no_plan_is_one_call_with_no_plan(chat_edge):
    chat_edge.responses.append(_response(False, "I don't know"))
    done = chat_edge.run("what is the leave policy?", None)
    assert chat_edge.seen["plans"] == [None]
    assert chat_edge.seen["dropped"] == []
    assert done["routing_reason"] == "best-match" and done["connected_providers"] is None


def test_a_normal_answer_carries_the_plan_unwidened(chat_edge):
    chat_edge.responses.append(_response(True, "Sana wrote it."))
    done = chat_edge.run("who wrote the leave policy?", _plan())
    (plan,) = chat_edge.seen["plans"]
    assert plan.cross is None
    assert done["routing_reason"] == "best-match"


def test_naming_another_tool_with_evidence_connects_up_front(chat_edge):
    chat_edge.responses.append(_response(True, "Yes, in #hr."))
    done = chat_edge.run("has the author discussed it in slack?", _plan())
    (plan,) = chat_edge.seen["plans"]
    assert plan.cross == frozenset({"notion", "slack"})
    assert plan.search == frozenset({"slack"})  # Slack's own search joins
    assert done["routing_reason"] == "graph-connected"
    assert done["connected_providers"] == ["notion", "slack"]
    assert chat_edge.seen["dropped"] == []


def test_a_refusal_escalates_once_and_replaces_its_turn(chat_edge):
    chat_edge.responses.extend([_response(False, "I don't know"), _response(True, "Found it.")])
    done = chat_edge.run("what did the author say about carry-over?", _plan())
    first, second = chat_edge.seen["plans"]
    assert first.cross is None and second.cross == frozenset({"notion", "slack"})
    assert chat_edge.seen["dropped"] == [
        (ORG, "conv-1", "what did the author say about carry-over?", "I don't know")
    ]
    assert done["answer"] == "Found it." and done["routing_reason"] == "graph-connected"
    assert chat_edge.seen["gaps"] == []


def test_a_withheld_document_never_escalates(chat_edge):
    withheld = _response(False, "not shared with you")
    withheld.access_restricted = True
    chat_edge.responses.append(withheld)
    chat_edge.run("what did the author say?", _plan())
    assert len(chat_edge.seen["plans"]) == 1


def test_connected_off_keeps_normal_answers_only(chat_edge, monkeypatch):
    monkeypatch.setenv("GRAPH_CONNECTED_ENABLED", "false")
    chat_edge.responses.append(_response(False, "I don't know"))
    done = chat_edge.run("has the author discussed it in slack?", _plan())
    assert [p.cross for p in chat_edge.seen["plans"]] == [None]
    assert done["connected_providers"] is None


def test_a_named_tool_connects_even_when_the_graph_never_reached_it(chat_edge):
    """Staging: the label said "Notion" although Slack answered -- the edge
    only counted tools the walk had reached. Naming a CONNECTED tool is enough."""
    notion_only = gp.GraphPlan(ORG, None, (), WalkResult(
        document_ids=["n1"], edges=1, document_providers={"n1": "notion"}))
    chat_edge.responses.append(_response(True, "Yes, in #rag-updates."))
    done = chat_edge.run("has the author discussed the scheduler in slack?", notion_only)
    assert done["connected_providers"] == ["notion", "slack"]
    assert chat_edge.seen["plans"][0].search == frozenset({"slack"})


def test_a_named_tool_that_is_not_connected_changes_nothing(chat_edge, monkeypatch):
    from app.api import chat

    monkeypatch.setattr(chat, "_connected_providers", lambda *a, **k: {"notion"})
    chat_edge.responses.append(_response(True, "An answer."))
    done = chat_edge.run("was it discussed in slack?", _plan())
    assert done["connected_providers"] is None
    assert chat_edge.seen["plans"][0].cross is None



def test_a_connected_answer_includes_the_tool_the_question_refers_to():
    """Real Gemini: "has the author of the Leave Policy discussed the scheduler
    in Slack?" routed to Linear refused -- the Leave Policy's author is a
    Notion fact, and Notion was not in the answer's tools."""
    plan = gp.GraphPlan(ORG, None, (Seed("e", "document", "Leave Policy", False, 0.9,
                                         "notion:leave"),))
    assert gp.connected_tools(plan, "linear", "slack") == {"linear", "slack", "notion"}
    assert plan.connected({"linear", "slack", "notion"}, search={"slack"}).search_tools(
        "linear") == ["slack"]  # Notion joins through the graph, not a second search


def test_a_connected_answer_is_framed_for_every_tool_it_reads():
    """Every per-tool profile says "only from <tool>", and the model obeyed it."""
    from app.rag.prompts import connected_prompt_profile

    profile = connected_prompt_profile({"linear", "slack"}, "linear")
    assert "Linear and Slack" in profile.persona
    assert profile.source_label == "linear"
    token = gp.use_plan(_plan().connected({"linear", "slack"}))
    try:
        assert rag_pipeline._connected_tools(ORG) == {"linear", "slack"}
    finally:
        gp.reset_plan(token)
    assert rag_pipeline._connected_tools(ORG) == frozenset()  # normal answers untouched
