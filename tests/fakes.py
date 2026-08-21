"""Deterministic in-memory fakes for Phase 8+ unit tests.

The Phase 8 mechanisms — incremental summarization and the retrieval-reuse gate —
are pure control-flow decisions, so they can (and should) be proven without a real
DB, LLM, or embedding model, exactly like the golden-set's deterministic path
checks. These fakes make the decisions observable and reproducible:

- ``RecordingLLM``   records every prompt and returns a canned reply per prompt
  kind (rewrite / summary / recovery / grounded answer), so we can inspect the
  *input* to the summarization call and assert its size never grows.
- ``KeywordEmbedder``  maps text to a vector over a fixed set of topic keywords, so
  cosine similarity between two texts is fully controllable (same topic → 1.0,
  disjoint topics → 0.0). This lets a test dictate whether the reuse gate fires.
- ``InMemoryConversationStore``  a full ``ConversationStore`` with no Postgres, incl.
  the Phase 8 ``set_last_retrieval`` / ``get_last_retrieval`` methods.
- ``RecordingVectorStore``  a minimal ``VectorStore`` whose ``query`` returns a
  distinctive chunk and counts calls, so a test can tell fresh retrieval from reuse.
- ``TopicAwareVectorStore``  scores stored chunks against the query embedding via
  the same keyword axes, so recovery expansions that introduce a topic keyword
  can retrieve a previously-missed chunk.
"""

from __future__ import annotations

import math

from app.core.exceptions import LLMProviderError
from app.llm.base import LLMProvider
from app.embeddings.base import EmbeddingProvider
from app.memory.base import (
    ConversationContext,
    ConversationStore,
    RetrievedChunkRecord,
    Turn,
)
from app.vectorstore.base import RetrievedChunk, VectorStore

# Fixed topic axes; a text's vector has a 1 on each topic keyword it contains.
TOPICS = ["leave", "sick", "remote", "parking", "dental", "wellness", "allowance"]


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        n = max(len(a), len(b))
        a = a + [0.0] * (n - len(a))
        b = b + [0.0] * (n - len(b))
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b))
    if denom == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / denom


class KeywordEmbedder(EmbeddingProvider):
    """Deterministic embedder: one axis per topic keyword, L2-normalized.

    Two texts sharing a topic keyword have cosine 1.0; texts with disjoint
    keywords have cosine 0.0 — so a test can precisely control the reuse gate.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            low = text.lower()
            vec = [1.0 if kw in low else 0.0 for kw in TOPICS]
            norm = math.sqrt(sum(v * v for v in vec))
            if norm == 0.0:
                # No known topic: a fixed unit vector orthogonal to all topics, so
                # it never accidentally matches a stored chunk.
                vec = [0.0] * len(TOPICS)
                out.append(vec + [1.0])
            else:
                out.append([v / norm for v in vec] + [0.0])
        return out


class RecordingLLM(LLMProvider):
    """Records prompts; returns a canned reply chosen by the prompt's kind."""

    model: str = "recording-test"

    def __init__(
        self,
        *,
        answer: str = "The answer is 25 days. [1]",
        answers: list[str] | None = None,
        summary: str = "Running summary of earlier turns.",
        rewrite: str | None = None,
        recovery_queries: list[str] | None = None,
        decompose_subquestions: list[str] | None = None,
        raise_on_recovery: bool = False,
        question_tone: str | None = "FACTUAL",
        empathy_opener: str | None = (
            "I'm sorry you're going through that — that sounds really hard."
        ),
        audit_verdict: str | None = "VERDICT: GROUNDED\nREASON: (none)",
    ) -> None:
        self.prompts: list[str] = []
        self._answer = answer
        self._answers = list(answers) if answers is not None else None
        self._answer_idx = 0
        self._summary = summary
        self._rewrite = rewrite
        self._recovery_queries = recovery_queries
        self._decompose_subquestions = decompose_subquestions
        self._raise_on_recovery = raise_on_recovery
        self._question_tone = question_tone
        self._empathy_opener = empathy_opener
        self._audit_verdict = audit_verdict
        self.tone_classify_calls = 0
        self.empathy_opener_calls = 0
        self.recovery_calls = 0
        self.decompose_calls = 0
        self.audit_calls = 0
        self.stages: list[str] = []
        from app.llm.usage import TokenUsage

        self.last_usage = TokenUsage(input_tokens=10, output_tokens=5)
        self.grounded_calls = 0

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.prompts.append(prompt)
        if "STANDALONE QUESTION:" in prompt:
            return self._rewrite or "How many paid annual leave days do we get?"
        if "UPDATED SUMMARY:" in prompt:
            return self._summary
        if "RETRIEVAL EXPRESSIONS:" in prompt:
            self.recovery_calls += 1
            if self._raise_on_recovery:
                raise LLMProviderError("simulated recovery expander failure")
            if self._recovery_queries is None:
                return ""
            return "\n".join(self._recovery_queries)
        if "SUB-QUESTIONS:" in prompt:
            self.decompose_calls += 1
            if self._decompose_subquestions is None:
                return "SINGLE"
            return "\n".join(self._decompose_subquestions)
        if "QUESTION_TONE_LABEL:" in prompt:
            self.tone_classify_calls += 1
            return self._question_tone or "FACTUAL"
        if prompt.rstrip().endswith("OPENER:") or "\nOPENER:" in prompt:
            self.empathy_opener_calls += 1
            return self._empathy_opener or ""
        if "DRAFT ANSWER:" in prompt:
            self.audit_calls += 1
            return self._audit_verdict or ""
        self.grounded_calls += 1
        if self._answers is not None:
            if self._answer_idx < len(self._answers):
                text = self._answers[self._answer_idx]
                self._answer_idx += 1
                return text
            return self._answers[-1] if self._answers else self._answer
        return self._answer

    @property
    def summary_prompts(self) -> list[str]:
        return [p for p in self.prompts if "UPDATED SUMMARY:" in p]

    @property
    def recovery_prompts(self) -> list[str]:
        return [p for p in self.prompts if "RETRIEVAL EXPRESSIONS:" in p]


class InMemoryConversationStore(ConversationStore):
    """A ``ConversationStore`` backed by dicts — no Postgres, fully deterministic."""

    def __init__(self) -> None:
        self._summaries: dict[str, str | None] = {}
        self._turns: dict[str, list[Turn]] = {}
        self._last: dict[str, list[RetrievedChunkRecord]] = {}
        self._seq = 0

    def create_conversation(self, org_id: str, workspace_id: str | None = None) -> str:
        self._seq += 1
        cid = f"conv-{self._seq}"
        self._summaries[cid] = None
        self._turns[cid] = []
        self._last[cid] = []
        return cid

    def append_turn(self, conversation_id: str, question: str, answer: str) -> int:
        turns = self._turns[conversation_id]
        index = (turns[-1].turn_index + 1) if turns else 0
        turns.append(Turn(turn_index=index, question=question, answer=answer))
        return index

    def get_turns(self, conversation_id: str) -> list[Turn]:
        return list(self._turns[conversation_id])

    def get_summary(self, conversation_id: str) -> str | None:
        return self._summaries.get(conversation_id)

    def get_context(self, conversation_id: str, recent_turns: int) -> ConversationContext:
        turns = self._turns[conversation_id]
        recent = turns[-recent_turns:] if recent_turns > 0 else []
        return ConversationContext(
            summary=self._summaries.get(conversation_id), recent_turns=list(recent)
        )

    def set_summary_and_prune(
        self, conversation_id: str, summary: str, keep_recent: int
    ) -> None:
        self._summaries[conversation_id] = summary
        turns = self._turns[conversation_id]
        self._turns[conversation_id] = turns[-keep_recent:] if keep_recent > 0 else []

    def set_last_retrieval(
        self, conversation_id: str, org_id: str, chunks: list[RetrievedChunkRecord]
    ) -> None:
        self._last[conversation_id] = list(chunks)

    def get_last_retrieval(self, conversation_id: str) -> list[RetrievedChunkRecord]:
        return list(self._last.get(conversation_id, []))


class RecordingVectorStore(VectorStore):
    """Minimal vector store: ``query`` returns one distinctive chunk, counts calls."""

    def __init__(self, org_id: str, content: str = "leave: 25 days") -> None:
        self._org_id = org_id
        self._content = content
        self.query_calls = 0

    def list_chunk_texts(self, org_id: str) -> list[str]:
        return [self._content] if org_id == self._org_id else []

    def create_organization(self, name: str) -> str:  # pragma: no cover - unused
        return self._org_id

    def add_document(self, *args, **kwargs) -> str:  # pragma: no cover - unused
        return "doc-fresh"

    def query(
        self,
        org_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        workspace_id: str | None = None,
        source_provider: str | None = None,
        date_range=None,
        tags=None,
    ) -> list[RetrievedChunk]:
        self.query_calls += 1
        return [
            RetrievedChunk(
                content=self._content,
                score=1.0,
                document_id="doc-fresh",
                chunk_index=0,
                org_id=org_id,
            )
        ]


class TopicAwareVectorStore(VectorStore):
    """Scores stored chunks against the query embedding on KeywordEmbedder axes.

    A first-pass query with no overlapping topic keywords scores ~0 (gate miss).
    A recovery expression that introduces a matching topic keyword retrieves the
    chunk with a high cosine — modelling a Retrieval Discovery Gap.
    """

    def __init__(
        self,
        org_id: str,
        chunks: list[tuple[str, str]] | None = None,
        *,
        weak_fallback_content: str | None = None,
        weak_fallback_score: float = 0.2,
    ) -> None:
        self._org_id = org_id
        self._chunks = list(chunks or [("doc-1", "leave wellness allowance: health benefits")])
        self._embedder = KeywordEmbedder()
        self._chunk_vecs = self._embedder.embed([c for _, c in self._chunks])
        self._weak_fallback_content = weak_fallback_content
        self._weak_fallback_score = weak_fallback_score
        self.query_calls = 0
        self.query_texts_via_embedding: list[list[float]] = []

    def list_chunk_texts(self, org_id: str) -> list[str]:
        return [c for _, c in self._chunks]

    def create_organization(self, name: str) -> str:  # pragma: no cover - unused
        return self._org_id

    def add_document(self, *args, **kwargs) -> str:  # pragma: no cover - unused
        return "doc-1"

    def query(
        self,
        org_id: str,
        query_embedding: list[float],
        top_k: int = 5,
        workspace_id: str | None = None,
        source_provider: str | None = None,
        date_range=None,
        tags=None,
    ) -> list[RetrievedChunk]:
        self.query_calls += 1
        self.query_texts_via_embedding.append(list(query_embedding))
        scored: list[RetrievedChunk] = []
        for (doc_id, content), vec in zip(self._chunks, self._chunk_vecs):
            score = _cosine(query_embedding, vec)
            scored.append(
                RetrievedChunk(
                    content=content,
                    score=score,
                    document_id=doc_id,
                    chunk_index=0,
                    org_id=org_id,
                )
            )
        scored.sort(key=lambda c: c.score, reverse=True)
        if scored and scored[0].score > 0.0:
            return scored[:top_k]
        if self._weak_fallback_content is not None:
            return [
                RetrievedChunk(
                    content=self._weak_fallback_content,
                    score=self._weak_fallback_score,
                    document_id="doc-weak",
                    chunk_index=0,
                    org_id=org_id,
                )
            ]
        return []
