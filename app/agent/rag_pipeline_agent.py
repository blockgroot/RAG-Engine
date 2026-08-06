"""Shared base for agents that answer by delegating to a ``RagPipeline``.

``PolicyAgent`` and ``WorkspaceAgent`` are both thin adapters over a
``RagPipeline`` — they differ only in *which* pipeline they're built with
(prompt persona, fallback copy, which capabilities are on), never in how a
``RagResult`` becomes an ``AgentResponse``. That one mapping lives here so it
is never duplicated between them (see ``policy_agent.py`` / ``workspace_agent.py``).
"""

from __future__ import annotations

from collections.abc import Iterator

from ..rag import RagPipeline
from ..rag.pipeline import RagResult
from ..vectorstore.base import RetrievedChunk
from .base import Agent, AgentResponse, Citation


class RagPipelineAgent(Agent):
    """An ``Agent`` that answers by delegating to an injected ``RagPipeline``.

    The pipeline is injected (not constructed here) so this stays a pure
    adapter and reuses the session-scoped provider fixtures in tests. Behavior
    is 100% the pipeline's — this class only reshapes the result.
    """

    def __init__(self, pipeline: RagPipeline) -> None:
        self._pipeline = pipeline

    @property
    def pipeline(self) -> RagPipeline:
        """The underlying RAG pipeline (exposed for diagnostics/settings access)."""
        return self._pipeline

    def answer(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
    ) -> AgentResponse:
        result = self._pipeline.answer(
            question,
            org_id=org_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        return self._to_response(result)

    def answer_stream(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[Iterator[str], AgentResponse]:
        """Like ``answer``, but the text arrives as a chunk iterator.

        Not part of the abstract ``Agent`` contract — it's a convenience for
        callers that want progressive display (the CLI, ``app/api/chat.py``'s
        streaming chat endpoint), not a capability every future agent must
        implement. See ``RagPipeline.answer_stream`` for why this chunks an
        already-fully-decided answer rather than streaming raw LLM tokens
        through the gate/recovery/tone-retry logic.
        """
        chunks, result = self._pipeline.answer_stream(
            question, org_id, conversation_id=conversation_id, workspace_id=workspace_id
        )
        return chunks, self._to_response(result)

    @staticmethod
    def _to_response(result: RagResult) -> AgentResponse:
        return AgentResponse(
            answer=result.answer,
            grounded=result.answered,
            source=result.source,
            citations=[RagPipelineAgent._to_citation(c) for c in result.sources],
            resolved_question=result.resolved_question,
            top_score=result.top_score,
            retrieval_reused=result.retrieval_reused,
            recovery_used=result.recovery_used,
            recovery_reason=result.recovery_reason,
            recovery_queries=list(result.recovery_queries),
            retrieval_improved=result.retrieval_improved,
            top_score_before=result.top_score_before,
            top_score_after=result.top_score_after,
            latency_ms=result.latency_ms,
            response_mode=result.response_mode,
            tone_retry_used=result.tone_retry_used,
        )

    @staticmethod
    def _display_chunk_content(content: str) -> str:
        """Drop the ingest-time contextual prefix for citation UI.

        Stored chunks are often ``"<LLM context>\n\n<original>"``. The prefix
        helps retrieval but reads like meta-commentary ("This chunk falls
        within…") in the sources panel — show the original excerpt instead.
        """
        text = (content or "").strip()
        if "\n\n" not in text:
            return text
        prefix, rest = text.split("\n\n", 1)
        rest = rest.strip()
        if not rest:
            return text
        # Only strip when the first block looks like a short situating blurb.
        if len(prefix) <= 400 and len(rest) >= len(prefix):
            return rest
        return text

    @staticmethod
    def _citation_reference(chunk: RetrievedChunk) -> str:
        """Human-readable locator — document title, never a raw UUID."""
        title = (chunk.document_title or "").strip()
        part = int(chunk.chunk_index) + 1  # 1-based for readers
        if title:
            return f"{title} · excerpt {part}"
        # Last resort if the JOIN missed (tests / odd stores): keep locator
        # stable but mark it as an excerpt, not a database id.
        return f"Document excerpt {part}"

    @staticmethod
    def _to_citation(chunk: RetrievedChunk) -> Citation:
        return Citation(
            content=RagPipelineAgent._display_chunk_content(chunk.content),
            reference=RagPipelineAgent._citation_reference(chunk),
            score=chunk.score,
        )
