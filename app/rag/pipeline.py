"""The RAG query path: question + org_id -> grounded, tenant-scoped answer.

Composes the Phase 1/2 pieces (embed -> org-scoped retrieve -> gate -> generate)
into the thing that actually answers a question. It is an *orchestrator*, not a
swappable provider — it owns no backend, just wires the existing interfaces — so
this package has ``pipeline`` + ``factory`` but no ``base.py`` (nothing to
abstract over). Two independent layers keep answers grounded:

* **Confidence gate (layer 1).** If the best retrieved chunk doesn't clear
  ``similarity_threshold``, return the fixed fallback *without calling the LLM*.
* **Strict prompt (layer 2).** When the gate passes, the prompt (``prompts.py``)
  forces answering only from context and emitting the same fallback when the
  context doesn't directly address the question.

The threshold defaults low (0.35) on purpose: it only rejects clear noise, and
the prompt makes the finer "on-topic but doesn't answer" call. Reasoning for the
value (and why it isn't yet trustworthy) is in CLAUDE.md §4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config.settings import RagSettings
from ..embeddings.base import EmbeddingProvider
from ..llm.base import LLMProvider
from ..vectorstore.base import RetrievedChunk, VectorStore
from .prompts import build_grounded_prompt


@dataclass(frozen=True)
class RagResult:
    """The outcome of answering one question for one tenant.

    - ``answer``     the text to show the user (a real answer, or the fixed
      fallback string when we could not ground an answer).
    - ``answered``   ``True`` only when the LLM produced a grounded answer.
      ``False`` for both refusal paths (gate short-circuit and LLM refusal), so
      callers can branch without string-matching.
    - ``sources``    the chunks retrieved for this question, all belonging to the
      queried ``org_id``. Populated whenever retrieval ran (i.e. except the empty
      case); this is what lets an answer be traced back to a tenant's own chunks.
    - ``top_score``  similarity of the best retrieved chunk (``None`` if nothing
      was retrieved), for logging / debugging / threshold tuning.
    """

    answer: str
    answered: bool
    sources: list[RetrievedChunk] = field(default_factory=list)
    top_score: float | None = None


class RagPipeline:
    """Composes embeddings + vector store + LLM into a grounded, org-scoped Q&A.

    Prefer building this via ``factory.build_rag_pipeline`` so configuration and
    provider wiring come from one place. Providers are injected (not constructed
    here) so the pipeline stays a pure orchestrator and is trivial to test.
    """

    def __init__(
        self,
        llm: LLMProvider,
        embedder: EmbeddingProvider,
        store: VectorStore,
        settings: RagSettings | None = None,
    ) -> None:
        self._llm = llm
        self._embedder = embedder
        self._store = store
        self._settings = settings or RagSettings.from_env()

    def answer(self, question: str, org_id: str) -> RagResult:
        """Answer ``question`` using only ``org_id``'s chunks, or refuse.

        Retrieval is delegated to the vector store, which enforces the
        ``WHERE org_id`` tenant filter — this pipeline never sees another
        tenant's data and never bypasses that guarantee.
        """
        settings = self._settings

        # 1) Embed the question and retrieve org-scoped candidates.
        query_vec = self._embedder.embed([question])[0]
        hits = self._store.query(org_id, query_vec, top_k=settings.top_k)

        # Nothing stored for this tenant (or nothing matched) -> refuse.
        if not hits:
            return RagResult(
                answer=settings.fallback_response,
                answered=False,
                sources=[],
                top_score=None,
            )

        top_score = hits[0].score

        # 2) Confidence gate (layer 1): best chunk isn't even on-topic -> refuse
        #    without spending an LLM call.
        if top_score < settings.similarity_threshold:
            return RagResult(
                answer=settings.fallback_response,
                answered=False,
                sources=hits,
                top_score=top_score,
            )

        # 3) Grounded generation (layer 2): the strict prompt decides whether the
        #    on-topic context actually answers the question.
        prompt = build_grounded_prompt(
            question=question,
            contexts=[h.content for h in hits],
            fallback_response=settings.fallback_response,
        )
        raw = self._llm.generate(prompt).strip()

        answered = not self._is_refusal(raw, settings.fallback_response)
        # Normalise an LLM refusal back to the exact fixed string, so callers see
        # one canonical fallback regardless of which layer produced it.
        answer = raw if answered else settings.fallback_response

        return RagResult(
            answer=answer,
            answered=answered,
            sources=hits,
            top_score=top_score,
        )

    @staticmethod
    def _is_refusal(text: str, fallback_response: str) -> bool:
        """True if the model's reply is (essentially) the fixed refusal sentence.

        Robust to trailing punctuation / whitespace / case and to the model
        wrapping the sentence, so a refusal is never mistaken for a real answer.
        """
        core = fallback_response.rstrip(".").strip().lower()
        return core in text.strip().lower()
