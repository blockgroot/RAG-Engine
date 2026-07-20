"""The RAG query path: question + org_id -> grounded, tenant-scoped answer.

This is where the Phase 1 (LLM + embeddings) and Phase 2 (tenant-isolated vector
store) pieces are composed into the thing that actually answers a question. It is
an *orchestrator*, not a swappable provider — it owns no backend of its own, it
wires the existing interfaces together — which is why this package has a
``pipeline`` + ``factory`` but no ``base.py`` abstract contract (there is nothing
to have multiple interchangeable backends of).

Flow::

    question, org_id
      -> embed(question)                       (EmbeddingProvider)
      -> store.query(org_id, ..., top_k)        (VectorStore; WHERE org_id filter)
      -> confidence gate on the top score       (layer 1 anti-hallucination)
      -> build_grounded_prompt(...)             (layer 2 anti-hallucination)
      -> llm.generate(prompt)                   (LLMProvider)
      -> RagResult(answer, answered, sources)

Two independent layers keep answers grounded:

* **The confidence gate (layer 1).** If the best retrieved chunk does not clear
  ``similarity_threshold``, we return the fixed fallback *without calling the LLM
  at all* — saving a call and refusing to reason over irrelevant context.
* **The strict prompt (layer 2).** When the gate passes, the prompt (see
  ``prompts.py``) forces the model to answer only from context and to emit the
  same fixed fallback when the context does not directly address the question.

**Why a single threshold cannot be trusted to do the whole job — and why the
default is 0.35.** Measured cosine similarity (BGE-M3), from only ~5 hand-picked
questions against this project's policy chunks — NOT a real evaluation set:

===============================================  ==================
question type                                    top-1 similarity
===============================================  ==================
directly answerable                              ~0.54 - 0.74
topically related but NOT actually answered      ~0.46 - 0.48
completely unrelated / noise                     ~0.30
===============================================  ==================

In *this* sample there is a gap between the "answerable" (>=~0.54) and
"related-but-unanswered" (~0.46-0.48) bands — but do NOT trust that gap: it is
far too little data. With more questions it is entirely plausible an
on-topic-but-unanswered case scores above 0.48, or a genuine answer below 0.54,
closing it. So no single threshold can be *relied on* to separate "answers" from
"on-topic but doesn't answer". We therefore set the gate low — at **0.35**, just
above pure noise (~0.30) and below the lowest relevant chunk we saw (~0.46) — so
it only rejects content that is not even on-topic, and hand the "on-topic but
doesn't answer" judgement to the strict prompt, which is far better at it than a
scalar cutoff. The value is deliberately conservative (few false refusals) and is
tunable via ``RAG_SIMILARITY_THRESHOLD`` without touching code. A golden-set
evaluation is what would actually validate 0.35; these few numbers only motivate
the choice, they don't confirm it.
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
