"""Query understanding for the Phase 10 retrieval RETRY — not run unconditionally.

The problem this solves: a user's question and a policy document's own vocabulary
often diverge — typos, abbreviations, casual phrasing, or simply a different word
for the same concept ("protein supplements reimbursed" vs a document that only
says "health-related products" under "Permissible Expenses"). Retrieval is a
similarity match against the ACTUAL text, so a vocabulary gap silently degrades
recall: the right chunk exists but never surfaces high enough to clear the
confidence gate, and the pipeline falls back even though the answer is present.

This class is only ever invoked by ``RagPipeline._attempt_retrieval_retry`` — a
single bounded retry that fires ONLY when the first (raw-question) retrieval
already failed the confidence gate, or passed the gate but evidence
classification still came back "none". The ordinary path (the common case) never
calls this and pays zero extra latency/cost for its existence.

This is an orchestrator-style component (like ``retrieval.py``), not a swappable
provider: there's exactly one way it's used (feed it an ``LLMProvider``, get back
a normalized query + expansions), so it has no ``base.py`` — following the same
convention as ``HybridRetriever``.

Design: ONE combined LLM call does both normalization and expansion, rather than
two separate calls, because both are cheap, bounded, structured sub-tasks with no
dependency requiring them to be sequential round trips. This directly satisfies
the "avoid unnecessary LLM calls" requirement while still solving the vocabulary
gap generically — no hardcoded domain categories anywhere in this file. It never
answers the question and never invents facts — its only output is a normalized
query plus a short list of alternate search phrases.

Failure mode: if the LLM call fails, times out, or returns malformed output, this
degrades to the original raw question with zero expansions — the caller then has
nothing new to retry with and falls straight back to the ordinary fallback, so a
failure here can never make retrieval worse than not retrying at all.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..core.exceptions import LLMProviderError
from ..llm.base import LLMProvider
from .prompts import build_query_understanding_prompt

# Extracts the first {...} block, tolerating a model wrapping JSON in prose or
# ```json fences despite being told not to (a common, harmless LLM habit).
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class UnderstoodQuery:
    """The output of query understanding: a cleaned-up query plus alternates.

    - ``normalized``  the typo/grammar-fixed, intent-preserving version of the
      question, used as the PRIMARY retrieval query and the one shown to the
      cross-encoder reranker (the reranker needs one canonical query text).
    - ``expansions``  additional retrieval-oriented phrasings using different
      plausible document vocabulary. Purely a retrieval aid — never shown to the
      user, never used for the final answer-generation prompt.
    """

    normalized: str
    expansions: list[str] = field(default_factory=list)

    def all_queries(self, max_total: int | None = None) -> list[str]:
        """Deduped query list, normalized first, optionally capped."""
        seen: set[str] = set()
        out: list[str] = []
        for q in [self.normalized, *self.expansions]:
            q = q.strip()
            key = q.lower()
            if q and key not in seen:
                seen.add(key)
                out.append(q)
        return out[:max_total] if max_total else out


def _parse_understood_query(raw: str, original_question: str) -> UnderstoodQuery:
    """Parse the model's JSON reply; degrade to the raw question on any issue."""
    match = _JSON_BLOCK_RE.search(raw)
    if not match:
        return UnderstoodQuery(normalized=original_question, expansions=[])

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return UnderstoodQuery(normalized=original_question, expansions=[])

    if not isinstance(data, dict):
        return UnderstoodQuery(normalized=original_question, expansions=[])

    normalized = data.get("normalized")
    normalized = str(normalized).strip() if normalized else ""
    if not normalized:
        normalized = original_question

    raw_expansions = data.get("expansions")
    expansions: list[str] = []
    if isinstance(raw_expansions, list):
        expansions = [str(e).strip() for e in raw_expansions if str(e).strip()]

    return UnderstoodQuery(normalized=normalized, expansions=expansions)


class QueryUnderstander:
    """Normalizes a query and proposes vocabulary-diverse retrieval expansions.

    Prefer building this via ``factory.build_rag_pipeline`` (wired in by default
    from ``QueryUnderstandingSettings``). The LLM is injected — usually the same
    instance the pipeline already uses, or optionally a distinct, lighter model
    (see ``QueryUnderstandingSettings.model``) since this is a much less demanding
    task than final grounded generation.
    """

    def __init__(self, llm: LLMProvider, max_expansions: int = 4) -> None:
        self._llm = llm
        self._max_expansions = max_expansions

    def understand(self, question: str) -> UnderstoodQuery:
        """Return a normalized query + up to ``max_expansions`` alternates.

        Never raises: any LLM failure or unparsable output degrades to the raw
        ``question`` with no expansions, so a failure here never breaks or
        degrades retrieval below its pre-Phase-10 behavior.
        """
        prompt = build_query_understanding_prompt(question, self._max_expansions)
        try:
            raw = self._llm.generate(prompt).strip()
        except LLMProviderError:
            return UnderstoodQuery(normalized=question, expansions=[])

        understood = _parse_understood_query(raw, question)
        if len(understood.expansions) > self._max_expansions:
            understood = UnderstoodQuery(
                normalized=understood.normalized,
                expansions=understood.expansions[: self._max_expansions],
            )
        return understood
