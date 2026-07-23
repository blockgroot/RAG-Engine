"""Query-understanding + expansion tests (Phase 10, pre-retrieval stage).

Deterministic unit tests — a fake LLM returning canned JSON, no real model —
proving the parsing/degradation logic itself:

- well-formed JSON is parsed into a normalized query + expansions;
- a model that wraps JSON in markdown fences or prose still parses (a common,
  harmless LLM habit);
- malformed/unparsable output degrades to the raw question with NO expansions,
  never raising and never breaking retrieval;
- expansions are capped at max_expansions and deduplicated (including against
  the normalized query itself);
- the stage never invokes anything beyond ``generate()`` — it cannot answer or
  fetch external facts.
"""

from __future__ import annotations

from app.core.exceptions import LLMProviderError
from app.llm.base import LLMProvider
from app.rag.query_understanding import QueryUnderstander, UnderstoodQuery


class _FixedLLM(LLMProvider):
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


class _RaisingLLM(LLMProvider):
    def generate(self, prompt: str) -> str:
        raise LLMProviderError("boom")


def test_parses_well_formed_json():
    llm = _FixedLLM(
        '{"normalized": "What is the reimbursement policy for protein supplements?", '
        '"expansions": ["health allowance", "permissible expenses", "wellness expenses"]}'
    )
    qu = QueryUnderstander(llm, max_expansions=4)

    result = qu.understand("can i get protien suppliments reimbersed?")

    assert result.normalized == "What is the reimbursement policy for protein supplements?"
    assert result.expansions == ["health allowance", "permissible expenses", "wellness expenses"]
    assert len(llm.prompts) == 1  # exactly ONE LLM call for both normalize + expand


def test_parses_json_wrapped_in_markdown_fence_and_prose():
    llm = _FixedLLM(
        "Sure, here you go:\n```json\n"
        '{"normalized": "How many sick days do I get?", "expansions": ["sick leave policy"]}'
        "\n```"
    )
    qu = QueryUnderstander(llm)

    result = qu.understand("how many sick days")

    assert result.normalized == "How many sick days do I get?"
    assert result.expansions == ["sick leave policy"]


def test_malformed_json_degrades_to_raw_question_with_no_expansions():
    llm = _FixedLLM("I cannot help with that.")
    qu = QueryUnderstander(llm)

    result = qu.understand("what about protein bars")

    assert result.normalized == "what about protein bars"
    assert result.expansions == []


def test_llm_failure_degrades_gracefully_without_raising():
    qu = QueryUnderstander(_RaisingLLM())

    result = qu.understand("does this crash?")

    assert result.normalized == "does this crash?"
    assert result.expansions == []


def test_missing_normalized_field_falls_back_to_original_question():
    llm = _FixedLLM('{"expansions": ["a", "b"]}')
    qu = QueryUnderstander(llm)

    result = qu.understand("original question")

    assert result.normalized == "original question"
    assert result.expansions == ["a", "b"]


def test_expansions_capped_at_max_expansions():
    llm = _FixedLLM(
        '{"normalized": "q", "expansions": ["a", "b", "c", "d", "e", "f"]}'
    )
    qu = QueryUnderstander(llm, max_expansions=2)

    result = qu.understand("q")

    assert result.expansions == ["a", "b"]


def test_all_queries_dedupes_case_insensitively_and_puts_normalized_first():
    understood = UnderstoodQuery(
        normalized="Health Allowance",
        expansions=["health allowance", "wellness expenses", "Wellness Expenses"],
    )

    assert understood.all_queries() == ["Health Allowance", "wellness expenses"]


def test_all_queries_respects_max_total_cap():
    understood = UnderstoodQuery(normalized="q", expansions=["a", "b", "c"])

    assert understood.all_queries(max_total=2) == ["q", "a"]
