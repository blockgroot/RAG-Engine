"""Retrieval-improvement tests (Phase 6): hybrid search, reranking, contextual.

Prove the three techniques do what they're meant to, on the real store/models:
- hybrid search catches an exact term a chunk contains even when it's buried
  among distractors (keyword signal, not just semantic);
- cross-encoder reranking pulls a relevant chunk that sits *outside* a naive
  top-k cutoff up to the top;
- a complete, multi-part question reliably surfaces ALL the relevant chunks;
- contextual retrieval prepends situating context to a chunk at ingest.

The Phase 2/3/5 suites re-run unchanged with this retrieval path underneath them
(see the `rag`, `rag_convo`, `rag_web` fixtures, now backed by HybridRetriever).
"""

from __future__ import annotations

import uuid

from app.config.settings import RagSettings, RetrievalSettings
from app.ingestion.contextualize import contextualize_chunk
from app.llm import build_llm_provider
from app.rag.retrieval import HybridRetriever
from app.vectorstore.base import RetrievedChunk
from .conftest import requires_db, requires_llm

# A corpus where the leave answer is split across two chunks, plus clearly
# unrelated distractors so top-k selection actually matters.
FULL_TIME = "Full-time employees are entitled to 25 days of paid annual leave per year."
PART_TIME = "Part-time employees receive 12 days of paid annual leave per year, pro-rated."
RARE_TERM = "Vision and dental benefits are provided under the ZephyrCare Platinum plan."
DISTRACTORS = [
    "The office kitchen is cleaned every Friday afternoon.",
    "Visitor parking is available in Lot C near the main entrance.",
    "Fire drills are conducted twice a year in spring and autumn.",
    "The staff dress code is smart-casual on weekdays.",
]


def _seed(store, embedder, org_cleanup, texts):
    org_id = store.create_organization(f"Retr Co-{uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    # One chunk per document so each fact is an independently-ranked chunk.
    for i, text in enumerate(texts):
        store.add_document(org_id, f"doc{i}", [text], embedder.embed([text]))
    return org_id


@requires_db
def test_hybrid_search_catches_exact_term(store, embedder, retriever, org_cleanup):
    org_id = _seed(store, embedder, org_cleanup, [RARE_TERM, *DISTRACTORS])
    qvec = embedder.embed(["ZephyrCare Platinum"])[0]

    # Keyword search matches the exact term directly...
    kw_hits = store.keyword_search(org_id, "ZephyrCare Platinum", qvec, top_k=10)
    assert any("ZephyrCare" in h.content for h in kw_hits), "keyword search missed the exact term"

    # ...and the hybrid retriever surfaces that chunk in its final results.
    result = retriever.retrieve(org_id, "ZephyrCare Platinum", qvec)
    assert any("ZephyrCare" in h.content for h in result.hits), "hybrid retrieval dropped the exact-term chunk"


@requires_db
def test_reranking_promotes_relevant_chunk_outside_naive_cutoff(reranker):
    query = "How many paid annual leave days do employees get?"
    # Deliberately bad order: the only relevant chunk is LAST, so a naive top-2
    # that preserved input order would drop it.
    candidates = [
        RetrievedChunk(DISTRACTORS[0], 0.5, "d", 0, "o"),
        RetrievedChunk(DISTRACTORS[1], 0.5, "d", 1, "o"),
        RetrievedChunk(FULL_TIME, 0.5, "d", 2, "o"),
    ]
    # Naive top-2 (input order) would exclude the relevant chunk:
    assert FULL_TIME not in [c.content for c in candidates[:2]]

    reranked = reranker.rerank(query, candidates, top_k=2)
    assert reranked[0].content == FULL_TIME, "reranker did not promote the relevant chunk to the top"


@requires_db
def test_complete_question_surfaces_all_relevant_chunks(store, embedder, reranker, org_cleanup):
    org_id = _seed(store, embedder, org_cleanup, [FULL_TIME, PART_TIME, *DISTRACTORS])
    # Tight top-k so coverage genuinely depends on good ranking, not on returning
    # the whole (small) corpus.
    retr = HybridRetriever(
        store=store,
        reranker=reranker,
        settings=RetrievalSettings(candidate_pool=30, hybrid_enabled=True, rerank_enabled=True),
        rag_settings=RagSettings(top_k=3, similarity_threshold=0.35, fallback_response="x"),
    )

    hits = retr.retrieve(
        org_id, "What is the paid annual leave allowance for full-time and part-time employees?",
        embedder.embed(["What is the paid annual leave allowance for full-time and part-time employees?"])[0],
    ).hits
    contents = " ".join(h.content for h in hits)

    # Both halves of the answer are present within the top-3.
    assert "25" in contents, f"full-time leave chunk missing from top-{len(hits)}"
    assert "12" in contents, f"part-time leave chunk missing from top-{len(hits)}"


@requires_llm
def test_contextual_retrieval_prepends_context():
    llm = build_llm_provider()
    document = (
        "# Acme Handbook\n\n## Paid Annual Leave — Part-Time Employees\n"
        "Part-time employees receive 12 days of paid annual leave per year, pro-rated."
    )
    chunk = "Part-time employees receive 12 days of paid annual leave per year, pro-rated."

    contextualized = contextualize_chunk(llm, document, chunk)

    # Original chunk is preserved, with extra situating context prepended.
    assert chunk in contextualized
    assert len(contextualized) > len(chunk), "expected context to be prepended"
