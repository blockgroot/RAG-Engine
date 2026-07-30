"""Ingest real Notion content into the vector store, scoped to an organization.

Lists every page the Notion integration has been shared with, converts each to
clean text, chunks + embeds it, and stores it under a freshly-created org. Prints
the org_id so you can immediately chat with it via scripts/cli.py.

Per-organization credentials (Phase 9): each real org has its OWN Notion internal
integration + secret, stored as ``NOTION_TOKEN_<NAME>`` in ``.env``. Point a run
at one with ``--token <name>``; the integration can only see pages shared with it,
so the org boundary is enforced by Notion, not just our code. With no ``--token``
the default ``NOTION_TOKEN`` is used (the single Phase 4 test org).

Setup (see the Phase 4/9 notes / README):
    1. Create a Notion internal integration per org; put each secret in
       ``NOTION_TOKEN_<NAME>`` (e.g. NOTION_TOKEN_ACME=ntn_...).
    2. Share that org's page(s) with that org's integration in Notion.
    3. python scripts/ingest_notion.py --org "Acme Corp" --token acme

Run:
    python scripts/ingest_notion.py                              # default token, default name
    python scripts/ingest_notion.py --org "Acme Corp" --token acme
"""

from __future__ import annotations

import argparse
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
    parser = argparse.ArgumentParser(description="Ingest Notion pages into an org.")
    parser.add_argument(
        "--org", default=DEFAULT_ORG_NAME, help="Organization name to create/ingest into."
    )
    parser.add_argument(
        "--token",
        default=None,
        metavar="NAME",
        help="Which per-org Notion token to use, i.e. the <NAME> in NOTION_TOKEN_<NAME> "
        "(case-insensitive). Omit to use the default NOTION_TOKEN.",
    )
    args = parser.parse_args()
    org_name = args.org

    try:
        which = f"NOTION_TOKEN_{args.token.upper()}" if args.token else "NOTION_TOKEN (default)"
        print(f"Connecting to Notion using {which} and preparing the store...")
        adapter = build_source_adapter("notion", token_name=args.token)
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
        result = ingest_source(adapter, org_id, provider="notion", store=store)

        print(
            f"\nDone: {result.documents_ingested} document(s), "
            f"{result.chunks_stored} chunk(s) stored, "
            f"{result.documents_skipped} skipped (empty)."
        )
        print("\nNow chat with this org:")
        print(f"  python scripts/cli.py {org_id}")
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
