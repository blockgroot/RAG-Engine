"""Deterministic in-memory fakes for Phase 8 unit tests.

The Phase 8 mechanisms — incremental summarization and the retrieval-reuse gate —
are pure control-flow decisions, so they can (and should) be proven without a real
DB, LLM, or embedding model, exactly like the golden-set's deterministic path
checks. These fakes make the decisions observable and reproducible:

- ``RecordingLLM``   records every prompt and returns a canned reply per prompt
  kind (rewrite / summary / grounded answer), so we can inspect the *input* to the
  summarization call and assert its size never grows.
- ``KeywordEmbedder``  maps text to a vector over a fixed set of topic keywords, so
  cosine similarity between two texts is fully controllable (same topic → 1.0,
  disjoint topics → 0.0). This lets a test dictate whether the reuse gate fires.
- ``InMemoryConversationStore``  a full ``ConversationStore`` with no Postgres, incl.
  the Phase 8 ``set_last_retrieval`` / ``get_last_retrieval`` methods.
- ``RecordingVectorStore``  a minimal ``VectorStore`` whose ``query`` returns a
  distinctive chunk and counts calls, so a test can tell fresh retrieval from reuse.
"""

from __future__ import annotations

import math

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
TOPICS = ["leave", "sick", "remote", "parking", "dental"]


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

    def __init__(
        self,
        *,
        answer: str = "The answer is 25 days. [1]",
        summary: str = "Running summary of earlier turns.",
        rewrite: str | None = None,
    ) -> None:
        self.prompts: list[str] = []
        self._answer = answer
        self._summary = summary
        self._rewrite = rewrite

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "STANDALONE QUESTION:" in prompt:
            # Echo a fixed standalone question (tests that need a specific rewrite
            # pass one in); default keeps the same topic as turn 1.
            return self._rewrite or "How many paid annual leave days do we get?"
        if "UPDATED SUMMARY:" in prompt:
            return self._summary
        return self._answer

    @property
    def summary_prompts(self) -> list[str]:
        return [p for p in self.prompts if "UPDATED SUMMARY:" in p]


class InMemoryConversationStore(ConversationStore):
    """A ``ConversationStore`` backed by dicts — no Postgres, fully deterministic."""

    def __init__(self) -> None:
        self._summaries: dict[str, str | None] = {}
        self._turns: dict[str, list[Turn]] = {}
        self._last: dict[str, list[RetrievedChunkRecord]] = {}
        self._seq = 0

    def create_conversation(self, org_id: str) -> str:
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

    def create_organization(self, name: str) -> str:  # pragma: no cover - unused
        return self._org_id

    def add_document(self, *args, **kwargs) -> str:  # pragma: no cover - unused
        return "doc-fresh"

    def query(
        self, org_id: str, query_embedding: list[float], top_k: int = 5
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
