"""RAGAS scoring for the answerable golden cases (the expensive, LLM-judged tier).

RAGAS scores four aspects of a grounded answer with an LLM-as-judge:

- **faithfulness**       — is every claim in the answer supported by the retrieved
  context? (the direct anti-hallucination signal)
- **answer relevancy**   — does the answer actually address the question?
- **context precision**  — were the retrieved chunks relevant (not padded with noise)?
- **context recall**     — did retrieval surface everything the reference answer needs?

WHY THIS IS ISOLATED AND OPTIONAL
---------------------------------
Each metric costs one-or-more LLM judge calls *per case*, so this is the "run less
often" tier (see evaluation/README.md). It is therefore:

- an **optional dependency** (``pip install -e '.[eval]'``) — the core runtime stays
  minimal (CLAUDE.md §1), and the every-push path-firing checks never import it;
- wired to the project's **own** OpenAI-compatible LLM endpoint and **local** BGE-M3
  embeddings, so scoring stays $0 / self-hostable — the same no-paid-dependency
  principle as the rest of the system, not a hosted judge API.

If RAGAS (or its langchain deps) isn't installed, ``ragas_available()`` returns
False and callers skip scoring with a clear message rather than crashing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config.settings import LLMSettings
from app.embeddings import build_embedding_provider

from .harness import CaseResult

# Friendly, stable metric labels -> reported everywhere in this order.
METRIC_LABELS = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]

# Baseline thresholds: a run is a regression if a mean metric drops below these.
# Calibrated as (first green-run mean − a safety margin) so normal LLM-judge
# variance doesn't flap the build; re-baseline if the model or corpus changes.
# See evaluation/README.md for the measured baseline these were derived from.
BASELINE: dict[str, float] = {
    "faithfulness": 0.80,
    "answer_relevancy": 0.80,
    "context_precision": 0.80,
    "context_recall": 0.80,
}


def ragas_available() -> bool:
    """True if RAGAS and its langchain deps import cleanly."""
    try:
        import ragas  # noqa: F401
        from langchain_openai import ChatOpenAI  # noqa: F401
    except Exception:
        return False
    return True


@dataclass(frozen=True)
class RagasReport:
    """Per-case metric scores + aggregate means + pass/fail vs the baseline."""

    per_case: dict[str, dict[str, float]] = field(default_factory=dict)
    means: dict[str, float] = field(default_factory=dict)
    baseline: dict[str, float] = field(default_factory=dict)

    @property
    def failures(self) -> list[str]:
        """Metrics whose mean fell below baseline (empty == all clear)."""
        return [m for m, base in self.baseline.items() if self.means.get(m, 0.0) < base]

    @property
    def passed(self) -> bool:
        return not self.failures


def _build_judge():
    """A RAGAS LLM wrapper over our configured OpenAI-compatible endpoint."""
    from langchain_openai import ChatOpenAI
    from ragas.llms import LangchainLLMWrapper

    s = LLMSettings.from_env()
    chat = ChatOpenAI(
        model=s.model or "auto",
        base_url=s.base_url,
        api_key=s.api_key or "not-needed",
        temperature=0.0,
        timeout=s.timeout,
    )
    return LangchainLLMWrapper(chat)


def _build_embeddings():
    """A RAGAS embeddings wrapper over our local BGE-M3 provider (no paid API)."""
    from langchain_core.embeddings import Embeddings
    from ragas.embeddings import LangchainEmbeddingsWrapper

    provider = build_embedding_provider()

    class _ProviderEmbeddings(Embeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return provider.embed(list(texts))

        def embed_query(self, text: str) -> list[float]:
            return provider.embed([text])[0]

    return LangchainEmbeddingsWrapper(_ProviderEmbeddings())


def score_cases(results: list[CaseResult]) -> RagasReport:
    """Score the grounded, policy-answered cases that carry a reference answer.

    Only cases with ``reference_answer`` set, ``source == "policy"``, ``grounded``,
    and at least one retrieved context are scored — i.e. the answerable cases (and
    the conversation follow-up, which is answerable once rewritten). Fallback and
    web cases are validated by the deterministic path checks, not by RAGAS.
    """
    from ragas import EvaluationDataset, SingleTurnSample, evaluate
    from ragas.metrics import (
        Faithfulness,
        LLMContextPrecisionWithReference,
        LLMContextRecall,
        ResponseRelevancy,
    )

    scorable = [
        r
        for r in results
        if r.case.reference_answer and r.source == "policy" and r.grounded and r.contexts
    ]
    if not scorable:
        return RagasReport(per_case={}, means={}, baseline=dict(BASELINE))

    samples = [
        SingleTurnSample(
            user_input=r.resolved_question or r.case.question,
            response=r.answer,
            retrieved_contexts=r.contexts,
            reference=r.case.reference_answer,
        )
        for r in scorable
    ]

    llm = _build_judge()
    embeddings = _build_embeddings()
    metrics = [
        Faithfulness(),
        ResponseRelevancy(),
        LLMContextPrecisionWithReference(),
        LLMContextRecall(),
    ]
    # Map RAGAS's internal metric names -> our stable friendly labels.
    name_to_label = {
        "faithfulness": "faithfulness",
        "answer_relevancy": "answer_relevancy",
        "llm_context_precision_with_reference": "context_precision",
        "context_recall": "context_recall",
    }

    result = evaluate(
        dataset=EvaluationDataset(samples=samples),
        metrics=metrics,
        llm=llm,
        embeddings=embeddings,
        show_progress=False,
    )
    df = result.to_pandas()

    per_case: dict[str, dict[str, float]] = {}
    for row_idx, r in enumerate(scorable):
        scores: dict[str, float] = {}
        for raw_name, label in name_to_label.items():
            if raw_name in df.columns:
                val = df.iloc[row_idx][raw_name]
                scores[label] = float(val) if val == val else float("nan")  # NaN-safe
        per_case[r.case.id] = scores

    means: dict[str, float] = {}
    for label in METRIC_LABELS:
        vals = [s[label] for s in per_case.values() if label in s and s[label] == s[label]]
        if vals:
            means[label] = sum(vals) / len(vals)

    return RagasReport(per_case=per_case, means=means, baseline=dict(BASELINE))
