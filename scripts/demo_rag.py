"""End-to-end RAG demo — wires together everything built so far.

Flow (all existing public APIs, composed):

    raw policy text
      -> preprocess()            (app.ingestion)
      -> chunk_text()            (app.ingestion)
      -> embed()                 (app.embeddings, local BGE-M3)
      -> add_document()          (app.vectorstore, stored under an org_id)

    question
      -> embed()                 (app.embeddings)
      -> query()                 (app.vectorstore, org-scoped retrieval)
      -> build a grounded prompt from the retrieved chunks
      -> generate()              (app.llm, the FreeLLMAPI/OpenAI-compatible model)
      -> printed answer + the sources it was grounded on

This is a DEMO, not the productionized pipeline (that becomes app/rag/ in a later
phase). It shows the full stack answering a question from stored, tenant-scoped
documents.

Run:
    python scripts/demo_rag.py
    python scripts/demo_rag.py "How many days of paid leave do employees get?"
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/demo_rag.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.config.settings import ChunkingSettings
from app.core.exceptions import ProviderError
from app.db import apply_schema
from app.embeddings import build_embedding_provider
from app.ingestion import chunk_text, preprocess
from app.llm import build_llm_provider
from app.vectorstore import build_vector_store

# A small sample policy document with a few distinct topics, so retrieval has to
# actually pick the relevant part rather than returning "the whole thing".
SAMPLE_POLICY = """
# Acme Corp Employee Handbook

## Paid Leave
Full-time employees are entitled to 25 days of paid annual leave per calendar
year. Up to 5 unused days may be carried over into the following year. Leave
requests must be submitted at least two weeks in advance through the HR portal.

## Remote Work
Employees may work remotely up to three days per week. Fully remote arrangements
require director approval and are reviewed every six months.

## Expense Reimbursement
Travel and business expenses are reimbursed up to $500 per trip. Original
receipts must be submitted within 30 days of the expense. Meals are capped at
$50 per day while travelling.

## Sick Leave
Employees receive 10 paid sick days per year, separate from annual leave. A
doctor's note is required for absences longer than three consecutive days.
"""

DEFAULT_QUESTION = "How many days of paid annual leave do employees get, and can they carry unused days over?"

# Smaller chunks than the default so the sample splits into several pieces and the
# retrieval step is actually meaningful in the demo.
DEMO_CHUNKING = ChunkingSettings(chunk_size=300, chunk_overlap=50)

TOP_K = 3


def build_rag_prompt(question: str, contexts: list[str]) -> str:
    numbered = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts))
    return (
        "You are a company policy assistant. Answer the QUESTION using ONLY the "
        "CONTEXT below. If the answer is not in the context, say you don't know. "
        "Cite the context numbers you used in square brackets.\n\n"
        f"CONTEXT:\n{numbered}\n\n"
        f"QUESTION: {question}\n\n"
        "ANSWER:"
    )


def main() -> int:
    load_dotenv()
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION

    try:
        # 0) Build the whole stack from config.
        print("Building providers (LLM, embeddings, vector store)...")
        llm = build_llm_provider()
        embedder = build_embedding_provider()
        store = build_vector_store()
        apply_schema()  # make sure the tables exist

        # 1) INGEST: preprocess -> chunk -> embed -> store, under a fresh org.
        org_id = store.create_organization("Acme Corp (demo)")
        print(f"\nCreated demo organization: {org_id}")

        chunks = chunk_text(preprocess(SAMPLE_POLICY), DEMO_CHUNKING)
        print(f"Split the policy into {len(chunks)} chunks; embedding + storing...")
        embeddings = embedder.embed(chunks)
        store.add_document(org_id, "Employee Handbook", chunks, embeddings)

        # 2) RETRIEVE: embed the question, fetch the most similar chunks (org-scoped).
        print(f"\nQuestion: {question}")
        print("Embedding the question and retrieving relevant chunks...")
        query_vec = embedder.embed([question])[0]
        hits = store.query(org_id, query_vec, top_k=TOP_K)

        print(f"\nTop {len(hits)} retrieved chunks:")
        for i, hit in enumerate(hits, 1):
            preview = hit.content.replace("\n", " ").strip()
            print(f"  [{i}] score={hit.score:.3f}  {preview[:90]}...")

        # 3) GENERATE: ground the LLM on the retrieved chunks.
        print("\nAsking the LLM to answer using only those chunks...\n")
        prompt = build_rag_prompt(question, [h.content for h in hits])
        answer = llm.generate(prompt)

        print("=" * 70)
        print("ANSWER:")
        print(answer.strip())
        print("=" * 70)
        print(f"\n(Grounded on {len(hits)} chunks from org {org_id}.)")
        return 0

    except ProviderError as exc:
        print(f"\nDemo FAILED: {exc}")
        if exc.cause:
            print(f"cause: {exc.cause}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
