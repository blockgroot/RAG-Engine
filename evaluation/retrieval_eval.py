"""Retrieval-only golden checks — no LLM, fast, deterministic (Phase 22).

Scores each case by **rank of the correct chunk** in the hybrid retriever's
final ranked list (after RRF + rerank). End-to-end eval can mask a retrieval
regression when generation still guesses correctly from weak context; this tier
catches ranking failures directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.rag.retrieval import HybridRetriever
from app.vectorstore.base import RetrievedChunk, VectorStore

from .harness import seed_corpus


@dataclass(frozen=True)
class RetrievalEvalCase:
    """One retrieval-only case."""

    id: str
    question: str
    content_markers: tuple[str, ...]
    max_rank: int = 3
    rationale: str = ""


RETRIEVAL_EVAL_CASES: list[RetrievalEvalCase] = [
    RetrievalEvalCase(
        id="annual-leave-days",
        question="How many days of paid annual leave do full-time employees get per year?",
        content_markers=("25", "full-time"),
        rationale="Core full-time leave chunk must rank in the top band.",
    ),
    RetrievalEvalCase(
        id="part-time-leave",
        question="How much paid annual leave do part-time employees receive?",
        content_markers=("12", "part-time"),
        rationale="Must outrank the full-time leave chunk.",
    ),
    RetrievalEvalCase(
        id="health-plan",
        question="What health and dental insurance plan does the company provide?",
        content_markers=("ZephyrCare",),
        rationale="Named entity exact-term recall via hybrid search.",
    ),
    RetrievalEvalCase(
        id="sick-leave-days",
        question="How many paid sick days do employees get per year?",
        content_markers=("10", "sick"),
        rationale="Separate leave type; must not pull annual-leave chunks first.",
    ),
    RetrievalEvalCase(
        id="expense-limit",
        question="What is the reimbursement limit for business travel expenses per trip?",
        content_markers=("500",),
        rationale="Distinct monetary fact in its own document.",
    ),
]


@dataclass(frozen=True)
class RetrievalEvalResult:
    case: RetrievalEvalCase
    rank: int | None
    top_contents: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.rank is not None and self.rank <= self.case.max_rank


def rank_of_correct_chunk(
    hits: list[RetrievedChunk], markers: tuple[str, ...]
) -> int | None:
    """1-based rank of the first hit whose content contains every marker."""
    for i, hit in enumerate(hits):
        low = hit.content.lower()
        if all(m.lower() in low for m in markers):
            return i + 1
    return None


def run_retrieval_case(
    retriever: HybridRetriever,
    embedder,
    org_id: str,
    case: RetrievalEvalCase,
) -> RetrievalEvalResult:
    qvec = embedder.embed([case.question])[0]
    retrieval = retriever.retrieve(org_id, case.question, qvec)
    rank = rank_of_correct_chunk(retrieval.hits, case.content_markers)
    tops = tuple(h.content[:120] for h in retrieval.hits[:3])
    return RetrievalEvalResult(case=case, rank=rank, top_contents=tops)


def run_retrieval_suite(
    retriever: HybridRetriever,
    embedder,
    org_id: str,
    cases: list[RetrievalEvalCase] | None = None,
) -> list[RetrievalEvalResult]:
    cases = cases or RETRIEVAL_EVAL_CASES
    return [run_retrieval_case(retriever, embedder, org_id, c) for c in cases]


def seed_eval_corpus(store: VectorStore, embedder, name: str) -> str:
    """Same deterministic corpus as the end-to-end golden set."""
    return seed_corpus(store, embedder, name)
