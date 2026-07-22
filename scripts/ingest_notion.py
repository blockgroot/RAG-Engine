"""Ingest real Notion content into the vector store, scoped to an organization.

Lists every page the Notion integration has been shared with, converts each to
clean text, chunks + embeds it, and stores it under a freshly-created org. Prints
the org_id so you can immediately query it with scripts/ask.py.

Setup (see the Phase 4 notes / README):
    1. Create a Notion internal integration, copy its secret into NOTION_TOKEN.
    2. Share your test page(s) with that integration in Notion.
    3. python scripts/ingest_notion.py ["Org Name"]

Run:
    python scripts/ingest_notion.py
    python scripts/ingest_notion.py "Acme Corp"
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/ingest_notion.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.core.exceptions import ProviderError
from app.db import apply_schema, close_pool
from app.ingestion import ingest_source
from app.sources import build_source_adapter
from app.vectorstore import build_vector_store

DEFAULT_ORG_NAME = "Notion Import (demo)"


def main() -> int:
    load_dotenv()
    org_name = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ORG_NAME

    try:
        print("Connecting to Notion and preparing the store...")
        adapter = build_source_adapter("notion")
        store = build_vector_store()
        apply_schema()

        # Show what the integration can see before ingesting.
        refs = adapter.list_documents()
        print(f"Notion integration can access {len(refs)} page(s):")
        for ref in refs:
            print(f"  - {ref.title}  ({ref.external_id})")
        if not refs:
            print(
                "\nNo pages found. In Notion, open your test page → '...' menu →\n"
                "'Connections' → add your integration, then re-run."
            )
            return 1

        org_id = store.create_organization(org_name)
        print(f"\nCreated organization '{org_name}': {org_id}")

        print("Ingesting (fetch → chunk → embed → store)...")
        result = ingest_source(adapter, org_id, store=store)

        print(
            f"\nDone: {result.documents_ingested} document(s), "
            f"{result.chunks_stored} chunk(s) stored, "
            f"{result.documents_skipped} skipped (empty)."
        )
        print("\nNow ask a question against this org:")
        print(f'  python scripts/ask.py {org_id} "your question here"')
        return 0

    except ProviderError as exc:
        print(f"\nIngestion FAILED: {exc}")
        if exc.cause:
            print(f"cause: {exc.cause}")
        return 1
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
