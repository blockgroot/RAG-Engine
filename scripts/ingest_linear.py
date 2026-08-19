"""Ingest real Linear issues into the vector store, scoped to an organization.

Lists every issue the Linear API key can see, flattens description + comments
to text, chunks + embeds it, and stores it under a freshly-created org. Same
per-org-token pattern as ``ingest_notion.py`` (Phase 9): each org gets its own
personal API key in ``LINEAR_TOKEN_<NAME>``.

Setup:
    1. Create a Linear personal API key (Settings -> Security & access).
    2. Put it in ``LINEAR_TOKEN_<NAME>`` (e.g. LINEAR_TOKEN_ACME=lin_api_...).
    3. python scripts/ingest_linear.py --org "Acme Corp" --token acme

Run:
    python scripts/ingest_linear.py                              # default token, default name
    python scripts/ingest_linear.py --org "Acme Corp" --token acme
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.core.exceptions import ProviderError
from app.db import apply_schema, close_pool
from app.ingestion import ingest_source
from app.sources import build_source_adapter
from app.vectorstore import build_vector_store

DEFAULT_ORG_NAME = "Linear Import (demo)"


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Ingest Linear issues into an org.")
    parser.add_argument(
        "--org", default=DEFAULT_ORG_NAME, help="Organization name to create/ingest into."
    )
    parser.add_argument(
        "--token",
        default=None,
        metavar="NAME",
        help="Which per-org Linear token to use, i.e. the <NAME> in LINEAR_TOKEN_<NAME> "
        "(case-insensitive). Omit to use the default LINEAR_TOKEN.",
    )
    args = parser.parse_args()
    org_name = args.org

    try:
        which = f"LINEAR_TOKEN_{args.token.upper()}" if args.token else "LINEAR_TOKEN (default)"
        print(f"Connecting to Linear using {which} and preparing the store...")
        adapter = build_source_adapter("linear", token_name=args.token)
        store = build_vector_store()
        apply_schema()

        refs = adapter.list_documents()
        print(f"Linear key can access {len(refs)} issue(s):")
        for ref in refs:
            print(f"  - {ref.title}  ({ref.external_id})")
        if not refs:
            print("\nNo issues found. Check the API key belongs to the right workspace.")
            return 1

        org_id = store.create_organization(org_name)
        print(f"\nCreated organization '{org_name}': {org_id}")

        print("Ingesting (fetch -> chunk -> embed -> store)...")
        result = ingest_source(adapter, org_id, provider="linear", store=store)

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
