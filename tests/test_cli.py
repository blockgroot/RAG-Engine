"""CLI wiring tests (Phase 9, Part 1).

These do NOT re-test the RAG/agent logic (covered elsewhere) — they only prove the
new single entry point (`scripts/cli.py`) correctly drives a ``PolicyAgent``: it
feeds each typed question to ``agent.answer`` with the right org + a stable
conversation id, stops on the exit words, and its per-turn rendering surfaces the
internals (rewrite / reuse / provenance / citations) without crashing.

The CLI is loaded from its file path (scripts/ is not an importable package), and
a fake agent + an in-memory console keep the tests deterministic and offline.
"""

from __future__ import annotations

import importlib.util
from io import StringIO
from pathlib import Path

from rich.console import Console

from app.agent.base import Agent, AgentResponse, Citation


def _load_cli():
    path = Path(__file__).resolve().parent.parent / "scripts" / "cli.py"
    spec = importlib.util.spec_from_file_location("cli_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


class FakeAgent(Agent):
    """Records every answer() call and returns a canned response."""

    def __init__(self, response: AgentResponse) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self._response = response

    def answer(self, question, org_id, *, conversation_id=None) -> AgentResponse:
        self.calls.append((question, org_id, conversation_id))
        return self._response


class FakeStreamingAgent(FakeAgent):
    """Also implements answer_stream (like PolicyAgent), chunking the canned answer."""

    def __init__(self, response: AgentResponse, chunk_size: int = 5) -> None:
        super().__init__(response)
        self.stream_calls: list[tuple[str, str, str | None]] = []
        self._chunk_size = chunk_size

    def answer_stream(self, question, org_id, *, conversation_id=None):
        self.stream_calls.append((question, org_id, conversation_id))
        text = self._response.answer

        def _chunks():
            for i in range(0, len(text), self._chunk_size):
                yield text[i : i + self._chunk_size]

        return _chunks(), self._response


def _prompts(*lines):
    """A prompt_fn that yields each line then signals end-of-session."""
    it = iter(lines)

    def _next():
        try:
            return next(it)
        except StopIteration:
            return None

    return _next


def _response(**kw) -> AgentResponse:
    base = dict(answer="Full-time staff get 25 days. [1]", grounded=True, source="policy")
    base.update(kw)
    return AgentResponse(**base)


def test_cli_drives_agent_with_org_and_stable_conversation_id():
    agent = FakeAgent(_response())
    console = Console(file=StringIO())

    turns = cli.converse(
        agent, "org-xyz", "conv-1", _prompts("first question?", "and a follow-up?"), console
    )

    assert turns == 2
    assert [c[0] for c in agent.calls] == ["first question?", "and a follow-up?"]
    # Every turn used the right org and the SAME conversation id (a real session).
    assert all(org == "org-xyz" for _, org, _ in agent.calls)
    assert all(cid == "conv-1" for _, _, cid in agent.calls)


def test_cli_stops_on_exit_word_and_skips_blank_lines():
    agent = FakeAgent(_response())
    console = Console(file=StringIO())

    turns = cli.converse(
        agent, "org-xyz", "conv-1", _prompts("  ", "real question?", "/exit", "never asked?"), console
    )

    assert turns == 1
    assert [c[0] for c in agent.calls] == ["real question?"]


def test_render_turn_surfaces_internals_and_citations():
    buf = StringIO()
    console = Console(file=buf, width=100)
    response = _response(
        resolved_question="What is the full-time annual leave allowance?",
        retrieval_reused=True,
        top_score=0.812,
        citations=[Citation(content="Full-time employees get 25 days.", reference="docA#0", score=0.81)],
    )

    cli.render_turn(console, "what about full-timers?", response)
    out = buf.getvalue()

    assert "grounded in policy documents" in out          # provenance label
    assert "rewritten to" in out                           # query-rewrite surfaced
    assert "reused" in out                                 # retrieval-reuse surfaced
    assert "0.812" in out                                  # gate score surfaced
    assert "docA#0" in out                                 # citation reference surfaced


def test_render_turn_labels_fallback_and_web_sources():
    for source, needle in [("none", "fallback"), ("web", "web search")]:
        buf = StringIO()
        cli.render_turn(Console(file=buf, width=100), "q?", _response(source=source, grounded=(source == "web")))
        assert needle in buf.getvalue()



def test_render_turn_surfaces_recovery():
    buf = StringIO()
    console = Console(file=buf, width=100)
    response = _response(
        recovery_used=True,
        recovery_reason="gate_miss",
        recovery_queries=["leave wellness allowance"],
        retrieval_improved=True,
        top_score=0.71,
        top_score_before=0.20,
        top_score_after=0.71,
        latency_ms=42.0,
    )
    cli.render_turn(console, "protein supplements?", response)
    out = buf.getvalue()
    assert "recovery" in out.lower()
    assert "gate_miss" in out
    assert "leave wellness allowance" in out


def test_converse_uses_answer_stream_when_agent_supports_it():
    agent = FakeStreamingAgent(_response())
    console = Console(file=StringIO())

    turns = cli.converse(
        agent, "org-xyz", "conv-1", _prompts("first question?"), console
    )

    assert turns == 1
    assert agent.stream_calls == [("first question?", "org-xyz", "conv-1")]
    assert agent.calls == []  # the blocking answer() path was never used


def test_converse_falls_back_to_answer_when_agent_has_no_answer_stream():
    agent = FakeAgent(_response())  # no answer_stream — plain Agent contract only
    console = Console(file=StringIO())

    turns = cli.converse(
        agent, "org-xyz", "conv-1", _prompts("first question?"), console
    )

    assert turns == 1
    assert agent.calls == [("first question?", "org-xyz", "conv-1")]


def test_stream_turn_reassembles_chunks_and_renders_full_answer():
    buf = StringIO()
    console = Console(file=buf, width=100)
    response = _response(
        answer="Full-time staff get 25 days. [1]",
        citations=[Citation(content="Full-time employees get 25 days.", reference="docA#0", score=0.81)],
    )
    agent = FakeStreamingAgent(response, chunk_size=6)

    result = cli.stream_turn(console, "how many days?", agent, "org-xyz", "conv-1")
    out = buf.getvalue()

    assert result is response
    assert "Full-time staff get 25 days." in out
    assert "grounded in policy documents" in out  # provenance label still shown
    assert "docA#0" in out  # citations still rendered after streaming completes
