"""Phase 18 before/after demos: chunking, BM25 ranks, compound retrieval.

Run from repo root (needs DATABASE_URL for BM25/compound sections):
    python scripts/measure_phase18.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.config.settings import ChunkingSettings
from app.db import close_pool, get_connection
from app.embeddings import build_embedding_provider
from app.ingestion import chunk_text, preprocess
from app.ingestion.chunk_tokens import count_tokens
from app.rag.factory import build_rag_pipeline
from app.config.settings import RecoverySettings
from app.vectorstore import build_vector_store

SAMPLE = """
# Health Allowance
The leave wellness allowance covers health-related products and supplements,
including protein powder when medically indicated.

Permissible reimbursements include gym memberships, mental-health apps, and
preventive screenings. Non-permissible items include cosmetic surgery, spa days,
and luxury goods.

Annual leave must be requested two weeks in advance through the HR portal.
""".strip()

COMPOUND = (
    "Can I get protein supplements reimbursed, and what else can I get reimbursed?"
)

WELLNESS = (
    "Leave wellness allowance covers health-related products and supplements "
    "including protein powder when prescribed."
)
OTHER = (
    "Permissible reimbursements include gym membership and therapy apps. "
    "Non-permissible: cosmetic surgery, spa treatments."
)


def _char_chunk_legacy(text: str, size: int = 1000, overlap: int = 150) -> list[str]:
    """Pre-Phase-18 character sizing (demo comparison only)."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return [c for c in chunks if c]


def _char_chunk_demo() -> None:
    tokenized = ChunkingSettings(chunk_size=256, chunk_overlap=40)
    clean = preprocess(SAMPLE)
    old = _char_chunk_legacy(clean)
    new = chunk_text(clean, tokenized)
    print("\n=== CHUNKING: character (legacy 1000/150) vs token (256/40) ===")
    print(f"Legacy chars: {len(old)} chunks; char lengths: {[len(c) for c in old]}")
    print(f"Token-based:  {len(new)} chunks; token counts: {[count_tokens(c) for c in new]}")
    for i, ch in enumerate(new[:2], 1):
        preview = ch.replace("\n", " ")[:120]
        print(f"  chunk {i}: {preview}...")


def _bm25_demo(org_id: str, embedder, store) -> None:
    query = "wellness supplements protein allowance"
    qvec = embedder.embed([query])[0]
    with get_connection() as conn:
        ts_rows = conn.execute(
            """
            SELECT content
            FROM chunks
            WHERE org_id = %s::uuid
              AND content_tsv @@ websearch_to_tsquery('english', %s)
            ORDER BY ts_rank(content_tsv, websearch_to_tsquery('english', %s)) DESC
            LIMIT 5
            """,
            (org_id, query, query),
        ).fetchall()
    bm25_hits = store.keyword_search(org_id, query, qvec, top_k=5)
    print("\n=== KEYWORD RANK: ts_rank vs Okapi BM25 (same FTS filter) ===")
    print(f"Query: {query}")
    for i, row in enumerate(ts_rows, 1):
        print(f"  ts_rank #{i}: {row[0][:90].replace(chr(10), ' ')}...")
    for i, hit in enumerate(bm25_hits, 1):
        print(f"  BM25    #{i}: {hit.content[:90].replace(chr(10), ' ')}...")


def _compound_demo(org_id: str) -> None:
    pipe = build_rag_pipeline(
        recovery_settings=RecoverySettings(enabled=True),
        memory=None,
        web_search=None,
    )
    result = pipe.answer(COMPOUND, org_id=org_id)
    print("\n=== COMPOUND QUESTION (full pipeline) ===")
    print(f"Question: {COMPOUND}")
    print(f"Decomposed: {result.question_decomposed} subs={result.sub_questions}")
    print(f"Answered: {result.answered} source={result.source} top_score={result.top_score}")
    print(f"Sources ({len(result.sources)}):")
    for i, s in enumerate(result.sources, 1):
        print(f"  [{i}] {s.content[:100].replace(chr(10), ' ')}...")
    print(f"Answer preview: {result.answer[:400]}...")


def main() -> int:
    load_dotenv()
    _char_chunk_demo()

    try:
        embedder = build_embedding_provider()
        store = build_vector_store()
    except Exception as exc:
        print(f"\n(Skip DB demos — provider/store unavailable: {exc})")
        return 0

    org_id = store.create_organization(f"Phase18-{uuid.uuid4().hex[:8]}")
    try:
        for title, text in [("wellness", WELLNESS), ("items", OTHER)]:
            chunks = chunk_text(preprocess(text))
            store.add_document(org_id, title, chunks, embedder.embed(chunks))
        _bm25_demo(org_id, embedder, store)
        _compound_demo(org_id)
    finally:
        with get_connection() as conn:
            conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))
        close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
