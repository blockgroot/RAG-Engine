"""The Slack Agent: answers only from this tenant's ingested Slack threads.

A retrieval agent over ``RagPipelineAgent``, separate for two reasons:

1. **Corpus.** Pinned to ``source_provider="slack"``. A relevance boundary,
   not an access one: ``org_id``/``workspace_id`` remain the isolation.
2. **Framing.** A passing "let's just do X" is not a handbook sentence, so
   it has its own ``SLACK_PROMPT_PROFILE`` and ``source`` label.

It adds one recency-based retry (``_recap``): "catch me up" resembles no
thread, so similarity retrieval refuses. When the ordinary path refuses,
it retries once with the newest threads, under a prompt that must still
refuse if they do not address the question.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from ..core.answer_sources import SOURCE_SLACK
from ..core.streaming import chunk_answer
from ..config.settings import SecuritySettings
from ..rag.cite import link_citations, strip_citations
from ..rag.prompts import build_slack_recap_prompt
from ..guard.moderation import answer_is_unsafe
from ..security.links import enforce_link_provenance
from ..security.untrusted import leaks_canary
from .base import AgentResponse
from ..vectorstore.base import Viewer
from .rag_pipeline_agent import RagPipelineAgent

logger = logging.getLogger(__name__)

# Chunks for a few days' briefing (roughly 10-20 conversations).
_RECAP_CHUNK_LIMIT = 40


def _channel_of(document_title: str | None) -> str | None:
    """Pull the channel name back out of a Slack document's ``"#chan: text"`` title."""
    title = (document_title or "").strip()
    if title.startswith("Thread in #"):
        return title[len("Thread in #") :].strip() or None
    if not title.startswith("#"):
        return None
    return title[1:].split(":", 1)[0].strip() or None


def _channel_id_of(external_id: str | None) -> str | None:
    eid = (external_id or "").strip()
    if not eid or ":" not in eid:
        return None
    return eid.partition(":")[0] or None


def _channel_names_for(org_id: str, workspace_id: str | None) -> dict[str, str]:
    """Best-effort Slack channel id → name map, for titles stored without the
    ``#channel:`` prefix. A failure yields an empty map."""
    try:
        from ..auth.credentials import get_connection_config

        config = get_connection_config(org_id, "slack", workspace_id=workspace_id) or {}
    except Exception:  # noqa: BLE001 - recap must still run from titles alone
        return {}
    names = config.get("channel_names") or {}
    if not isinstance(names, dict):
        return {}
    return {str(k): str(v) for k, v in names.items() if v}


class SlackAgent(RagPipelineAgent):
    """Answers questions from ingested Slack threads only."""

    def answer(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        viewer: Viewer | None = None,
    ) -> AgentResponse:
        response = super().answer(
            question,
            org_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            viewer=viewer,
        )
        if response.grounded:
            return response
        return self._recap(question, org_id, workspace_id=workspace_id, viewer=viewer) or response

    def answer_stream(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        viewer: Viewer | None = None,
    ) -> tuple[Iterator[str], AgentResponse]:
        chunks, response = super().answer_stream(
            question,
            org_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
            viewer=viewer,
        )
        if response.grounded:
            return chunks, response
        recap = self._recap(question, org_id, workspace_id=workspace_id, viewer=viewer)
        if recap is None:
            return chunks, response
        # The similarity answer is discarded, so its chunk iterator is too.
        return chunk_answer(recap.answer), recap

    def _recap(
        self,
        question: str,
        org_id: str,
        *,
        workspace_id: str | None,
        viewer: Viewer | None = None,
    ) -> AgentResponse | None:
        """One recency-selected retry. ``None`` means "keep the original answer".

        Every failure returns ``None``, so this can only turn a refusal into a
        grounded answer, never the reverse.
        """
        pipeline = self.pipeline
        try:
            chunks = pipeline.recent_chunks_for_recap(
                org_id,
                workspace_id=workspace_id,
                limit=_RECAP_CHUNK_LIMIT,
                viewer=viewer,
            )
        except NotImplementedError:
            return None
        except Exception:  # noqa: BLE001 - a recap failure must not break the answer
            logger.warning("Slack recap retrieval failed", exc_info=True)
            return None
        if not chunks:
            return None

        fallback = pipeline.fallback_response
        names = _channel_names_for(org_id, workspace_id)
        pairs = []
        for c in chunks:
            channel = _channel_of(c.document_title)
            if not channel:
                cid = _channel_id_of(c.source_external_id)
                channel = (names.get(cid) if cid else None) or cid
            pairs.append((c.content, channel))
        prompt = build_slack_recap_prompt(question, pairs, fallback)
        try:
            text = (pipeline.generate_raw(prompt) or "").strip()
        except Exception:  # noqa: BLE001
            logger.warning("Slack recap generation failed", exc_info=True)
            return None
        if not text or fallback.lower() in text.lower():
            return None
        if leaks_canary(text):
            logger.warning("security.canary_leak stage=slack_recap")
            return None
        text = enforce_link_provenance(
            text, [c.content for c in chunks], SecuritySettings.from_env().link_allowlist
        )
        # The recap prompt numbers threads in this same order, so block i is chunks[i].
        text, cited = link_citations(text, list(chunks))
        if answer_is_unsafe(strip_citations(text), org_id=None, stage="slack_recap"):
            return None

        return AgentResponse(
            answer=text,
            grounded=True,
            source=SOURCE_SLACK,
            citations=[self._to_citation(c) for c in chunks],
            resolved_question=question,
            cited=cited,
        )
