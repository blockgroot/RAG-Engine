"""Step-by-step walkthrough of the productionized RAG pipeline (app/rag/).

Unlike ``demo_rag.py`` (which hand-wires the stack with its own prompt), this
drives the *real* ``RagPipeline`` and narrates every stage so you can watch the
end-to-end flow and each intermediate result:

    ingest sample policy  -> preprocess -> chunk -> embed -> store (fresh org)
    question              -> embed          (show vector dimension)
                          -> org-scoped retrieve  (show each chunk + similarity)
                          -> confidence gate       (show top_score vs threshold)
                          -> grounded prompt        (show the exact prompt sent)
                          -> LLM generate           (show the answer + answered flag)

The numbers shown ARE the pipeline's own: we call ``rag.answer()`` for the
authoritative ``RagResult`` and narrate from its public fields (``sources``,
``top_score``, ``answered``). The prompt is rebuilt with the same public builder
purely for display — identical inputs, identical prompt.

Run:
    python scripts/demo_pipeline.py
    python scripts/demo_pipeline.py "How many sick days do employees get?"
    # a question the policy does not answer -> watch it fall back:
    python scripts/demo_pipeline.py "What is the parental leave policy?"
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/demo_pipeline.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.config.settings import ChunkingSettings, RagSettings
from app.core.exceptions import ProviderError
from app.db import apply_schema, close_pool
from app.embeddings import build_embedding_provider
from app.ingestion import chunk_text, preprocess
from app.rag import build_grounded_prompt, build_rag_pipeline
from app.vectorstore import build_vector_store

SAMPLE_POLICY = """
# Acme Corp Employee Handbook

## Paid Leave
Full-time employees are entitled to 25 days of paid annual leave per calendar
year. Up to 5 unused days may be carried over into the following year.

## Expense Reimbursement
Travel and business expenses are reimbursed up to $500 per trip. Original
receipts must be submitted within 30 days of the expense.

## Sick Leave
Employees receive 10 paid sick days per year, separate from annual leave. A
doctor's note is required for absences longer than three consecutive days.
"""

DEFAULT_QUESTION = (
    "How many days of paid annual leave do employees get, and can they "
    "carry unused days over?"
)

# Smaller chunks so the sample splits into several pieces and retrieval is
# actually meaningful in the demo.
DEMO_CHUNKING = ChunkingSettings(chunk_size=300, chunk_overlap=50)


def _rule(char: str = "-") -> str:
    return char * 72


def main() -> int:
    load_dotenv()
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    settings = RagSettings.from_env()

    try:
        # 0) Build the whole stack from config and wire the pipeline.
        print("Building providers + RAG pipeline from config...")
        embedder = build_embedding_provider()
        store = build_vector_store()
        apply_schema()
        rag = build_rag_pipeline(embedder=embedder, store=store, settings=settings)

        # 1) INGEST: preprocess -> chunk -> embed -> store, under a fresh org.
        print(_rule("="))
        print("STEP 1 — INGEST (one-time setup for this demo)")
        print(_rule("="))
        org_id = store.create_organization("Acme Corp (demo)")
        chunks = chunk_text(preprocess(SAMPLE_POLICY), DEMO_CHUNKING)
        embeddings = embedder.embed(chunks)
        store.add_document(org_id, "Employee Handbook", chunks, embeddings)
        print(f"org_id            : {org_id}")
        print(f"chunks stored     : {len(chunks)}")
        print(f"embedding dim     : {len(embeddings[0])}")

        # 2) EMBED the question (shown separately just to expose the vector).
        print("\n" + _rule("="))
        print("STEP 2 — EMBED THE QUESTION")
        print(_rule("="))
        query_vec = embedder.embed([question])[0]
        print(f"question          : {question}")
        print(f"vector dimension  : {len(query_vec)}")
        print(f"first 5 values    : {[round(float(v), 4) for v in query_vec[:5]]}")

        # Authoritative run: everything below is narrated from THIS result.
        result = rag.answer(question, org_id=org_id)

        # 3) RETRIEVE: org-scoped chunks + their similarity scores.
        print("\n" + _rule("="))
        print("STEP 3 — RETRIEVE (org-scoped, top_k = %d)" % settings.top_k)
        print(_rule("="))
        if not result.sources:
            print("no chunks retrieved for this org.")
        for i, hit in enumerate(result.sources, 1):
            preview = hit.content.replace("\n", " ").strip()
            print(f"  [{i}] score={hit.score:.3f}  org={hit.org_id}  {preview[:70]}...")

        # 4) CONFIDENCE GATE (layer 1): top_score vs threshold.
        print("\n" + _rule("="))
        print("STEP 4 — CONFIDENCE GATE (layer 1)")
        print(_rule("="))
        print(f"top_score         : {result.top_score}")
        print(f"threshold         : {settings.similarity_threshold}")
        if result.top_score is None:
            print("decision          : BLOCKED (nothing retrieved) -> fallback, no LLM call")
        elif result.top_score < settings.similarity_threshold:
            print("decision          : BLOCKED (below threshold) -> fallback, no LLM call")
        else:
            print("decision          : PASSED -> hand off to the strict prompt (layer 2)")

        # 5) GROUNDED PROMPT (layer 2): show the exact prompt when the gate passed.
        gate_passed = (
            result.top_score is not None
            and result.top_score >= settings.similarity_threshold
        )
        if gate_passed:
            print("\n" + _rule("="))
            print("STEP 5 — GROUNDED PROMPT SENT TO THE LLM (layer 2)")
            print(_rule("="))
            prompt = build_grounded_prompt(
                question=question,
                contexts=[s.content for s in result.sources],
                fallback_response=settings.fallback_response,
            )
            print(prompt)

        # 6) FINAL RESULT.
        print("\n" + _rule("="))
        print("STEP 6 — RESULT")
        print(_rule("="))
        print(f"answered          : {result.answered}")
        print(f"answer            : {result.answer}")
        print(f"grounded on       : {len(result.sources)} chunk(s) from org {org_id}")
        return 0

    except ProviderError as exc:
        print(f"\nDemo FAILED: {exc}")
        if exc.cause:
            print(f"cause: {exc.cause}")
        return 1
    finally:
        # Release pooled DB connections at this process boundary.
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
