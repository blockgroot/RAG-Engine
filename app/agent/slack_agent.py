"""The Slack Agent: answers only from this tenant's ingested Slack threads.

Slack is ingested like Notion/Drive (fetch → chunk → embed → store), so unlike
``GitHubAgent`` this is a genuine retrieval agent and reuses ``RagPipelineAgent``
exactly as ``PolicyAgent``/``WorkspaceAgent`` do. What makes it a separate agent
rather than another caller of ``PolicyAgent`` is two things the policy pipeline
cannot express at once:

1. **Corpus.** Its pipeline is pinned to ``source_provider="slack"``, so
   retrieval reaches only chunks whose document came from Slack. A tab labelled
   "Slack" that answered from a Notion page would be lying about where the
   answer came from — and the user cannot tell the difference from the answer
   text alone. This is a *relevance* boundary, not an access one: everything it
   can reach was already inside the caller's ``org_id``/``workspace_id``, which
   remain the only isolation guarantees (see ``app/vectorstore/base.py``).

2. **Framing.** Chat threads are not documents. People in a Slack thread think
   out loud, disagree, and change their minds, so a passing "let's just do X"
   is not the same kind of claim as a sentence in a handbook — and answering
   with the policy persona would launder one into the other. Hence its own
   ``SLACK_PROMPT_PROFILE`` (see ``app/rag/prompts.py``) and its own
   ``source`` label, so a caller can always tell a Slack-derived answer apart.

Everything else — the confidence gate, the strict grounded prompt, reranking,
recovery, memory — is the unchanged shared machinery.

It does add ONE thing ``PolicyAgent`` deliberately does not: a recency-based
second attempt (``_recap``). That is not a policy exception sneaking in, it is
a property of the corpus. Every retrieval path here ranks chunks by similarity
to the question, which works because a handbook page about leave *resembles* a
question about leave. A recap request — "catch me up on the last few days" —
resembles nothing in particular, so similarity returns arbitrary threads and
the grounded prompt correctly refuses (measured on live data: top score 0.516,
well clear of the 0.35 gate, refused by the prompt). Documents do not generate
that question shape; a chat log does, constantly. So when the ordinary path
refuses, this agent retries once with evidence selected by *recency* instead,
under a prompt that must still refuse if those threads do not address the
question. Bounded at one extra attempt, same as Phase 3's recovery.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from ..core.answer_sources import SOURCE_SLACK
from ..core.streaming import chunk_answer
from ..rag.prompts import build_slack_recap_prompt
from .base import AgentResponse
from .rag_pipeline_agent import RagPipelineAgent

logger = logging.getLogger(__name__)

# Enough threads to brief someone who has been away for a few days without
# building a prompt the aux model has to wade through. Chunks, not threads —
# a long thread is several — so this is roughly 10-20 conversations.
_RECAP_CHUNK_LIMIT = 40


def _channel_of(document_title: str | None) -> str | None:
    """Pull the channel name back out of a Slack document's ``"#chan: text"`` title."""
    title = (document_title or "").strip()
    if not title.startswith("#"):
        return None
    return title[1:].split(":", 1)[0].strip() or None


class SlackAgent(RagPipelineAgent):
    """Answers questions from ingested Slack threads only."""

    def answer(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
    ) -> AgentResponse:
        response = super().answer(
            question,
            org_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        if response.grounded:
            return response
        return self._recap(question, org_id, workspace_id=workspace_id) or response

    def answer_stream(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
    ) -> tuple[Iterator[str], AgentResponse]:
        chunks, response = super().answer_stream(
            question,
            org_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        if response.grounded:
            return chunks, response
        recap = self._recap(question, org_id, workspace_id=workspace_id)
        if recap is None:
            return chunks, response
        # The similarity answer is discarded, so its chunk iterator must be
        # too — streaming a refusal and then replacing it is worse than not
        # streaming it at all.
        return chunk_answer(recap.answer), recap

    def _recap(
        self, question: str, org_id: str, *, workspace_id: str | None
    ) -> AgentResponse | None:
        """One recency-selected retry. ``None`` means "keep the original answer".

        Every failure path returns ``None`` rather than raising or inventing:
        a store without recency support, an empty corpus, an LLM error, or a
        model that (correctly) declines. The caller then serves the ordinary
        refusal, so this can only ever turn a refusal into a grounded answer,
        never the reverse.
        """
        pipeline = self.pipeline
        try:
            chunks = pipeline.recent_chunks_for_recap(
                org_id, workspace_id=workspace_id, limit=_RECAP_CHUNK_LIMIT
            )
        except NotImplementedError:
            return None
        except Exception:  # noqa: BLE001 - a recap failure must not break the answer
            logger.warning("Slack recap retrieval failed", exc_info=True)
            return None
        if not chunks:
            return None

        fallback = pipeline.fallback_response
        pairs = [(c.content, _channel_of(c.document_title)) for c in chunks]
        prompt = build_slack_recap_prompt(question, pairs, fallback)
        try:
            text = (pipeline.generate_raw(prompt) or "").strip()
        except Exception:  # noqa: BLE001
            logger.warning("Slack recap generation failed", exc_info=True)
            return None
        if not text or fallback.lower() in text.lower():
            return None

        return AgentResponse(
            answer=text,
            grounded=True,
            source=SOURCE_SLACK,
            citations=[self._to_citation(c) for c in chunks],
            resolved_question=question,
        )
