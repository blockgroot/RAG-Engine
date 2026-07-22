"""Before/after retrieval comparison (Phase 6).

Runs the SAME question two ways against one org's stored chunks and prints the
top-k each returns, so the retrieval difference is visible:

- BEFORE: plain top-k vector similarity (the Phase 3 behaviour).
- AFTER : hybrid (vector + keyword, RRF-fused) then cross-encoder reranked.

Run:
    python scripts/compare_retrieval.py <org_id> "your question here"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.config.settings import RagSettings, RetrievalSettings
from app.core.exceptions import ProviderError
from app.db import close_pool
from app.embeddings import build_embedding_provider
from app.rag.retrieval import HybridRetriever
from app.reranker import build_reranker
from app.vectorstore import build_vector_store

TOP_K = 3


def _show(title: str, hits) -> None:
    print(f"\n{title}")
    if not hits:
        print("  (nothing retrieved)")
        return
    for i, h in enumerate(hits, 1):
        preview = h.content.replace("\n", " ").strip()[:95]
        print(f"  {i}. (cos={h.score:.3f}) {preview}")


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 3:
        print('Usage: python scripts/compare_retrieval.py <org_id> "<question>"')
        return 2
    org_id, question = sys.argv[1], " ".join(sys.argv[2:])

    try:
        embedder = build_embedding_provider()
        store = build_vector_store()
        qvec = embedder.embed([question])[0]

        print(f"Question: {question}")

        # BEFORE — plain top-k vector search.
        before = store.query(org_id, qvec, top_k=TOP_K)
        _show(f"BEFORE — plain vector top-{TOP_K}:", before)

        # AFTER — hybrid + reranking, same final top-k.
        retriever = HybridRetriever(
            store=store,
            reranker=build_reranker(),
            settings=RetrievalSettings(candidate_pool=30, hybrid_enabled=True, rerank_enabled=True),
            rag_settings=RagSettings(top_k=TOP_K, similarity_threshold=0.35, fallback_response="x"),
        )
        after = retriever.retrieve(org_id, question, qvec).hits
        _show(f"AFTER  — hybrid + rerank top-{TOP_K}:", after)
        return 0

    except ProviderError as exc:
        print(f"\nComparison FAILED: {exc}")
        if exc.cause:
            print(f"cause: {exc.cause}")
        return 1
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
