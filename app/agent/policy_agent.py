"""The Policy Agent: the company-policy Q&A agent, as a formal unit.

Phase 7 extracts the retrieve → gate → generate → memory → web-search-fallback
behavior that previously lived scattered across the CLI scripts into one place.
The logic is **not rewritten** — it still lives, exactly as before, in
``RagPipeline`` (Phases 3–6). ``PolicyAgent`` is a thin adapter that:

- composes an already-built ``RagPipeline`` (injected, like every other
  orchestrator here), and
- maps its ``RagResult`` onto the generic ``AgentResponse`` / ``Citation`` shape,

so the one place callers (scripts, the eval harness, a future HTTP layer) reach for
policy answers is this class — while the gate, prompt, and every test outcome stay
byte-for-byte unchanged, because the pipeline underneath is untouched.
"""

from __future__ import annotations

from ..rag import RagPipeline
from ..rag.pipeline import RagResult
from ..vectorstore.base import RetrievedChunk
from .base import Agent, AgentResponse, Citation


class PolicyAgent(Agent):
    """Answers policy questions for a tenant by delegating to a ``RagPipeline``.

    Prefer building via ``factory.build_policy_agent``. The pipeline is injected so
    this stays a pure adapter (and reuses the session-scoped provider fixtures in
    tests). Behavior is 100% the pipeline's — this class only reshapes the result.
    """

    def __init__(self, pipeline: RagPipeline) -> None:
        self._pipeline = pipeline

    @property
    def pipeline(self) -> RagPipeline:
        """The underlying RAG pipeline (exposed for diagnostics/settings access)."""
        return self._pipeline

    def answer(
        self, question: str, org_id: str, *, conversation_id: str | None = None
    ) -> AgentResponse:
        result = self._pipeline.answer(
            question, org_id=org_id, conversation_id=conversation_id
        )
        return self._to_response(result)

    @staticmethod
    def _to_response(result: RagResult) -> AgentResponse:
        return AgentResponse(
            answer=result.answer,
            grounded=result.answered,
            source=result.source,
            citations=[PolicyAgent._to_citation(c) for c in result.sources],
            resolved_question=result.resolved_question,
            top_score=result.top_score,
            retrieval_reused=result.retrieval_reused,
        )

    @staticmethod
    def _to_citation(chunk: RetrievedChunk) -> Citation:
        return Citation(
            content=chunk.content,
            reference=f"{chunk.document_id}#{chunk.chunk_index}",
            score=chunk.score,
        )
