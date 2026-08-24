"""Ingest real Confluence pages into the vector store, scoped to an organization.

Lists every page the Confluence credential can see, converts each to plain
text, chunks + embeds it, and stores it under a freshly-created org. Same
per-org-credential pattern as ``ingest_notion.py`` / ``ingest_linear.py``:
each org gets its own ``CONFLUENCE_BASE_URL_<NAME>`` / ``_EMAIL_<NAME>`` /
``_TOKEN_<NAME>`` triple.

Setup:
    1. Create an Atlassian API token: id.atlassian.com/manage-profile/security/api-tokens
    2. Put the site URL / your email / the token in:
       CONFLUENCE_BASE_URL_ACME=https://acme.atlassian.net/wiki
       CONFLUENCE_EMAIL_ACME=you@acme.com
       CONFLUENCE_TOKEN_ACME=<api token>
    3. python scripts/ingest_confluence.py --org "Acme Corp" --token acme

Run:
    python scripts/ingest_confluence.py                              # default credential
    python scripts/ingest_confluence.py --org "Acme Corp" --token acme
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

DEFAULT_ORG_NAME = "Confluence Import (demo)"


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Ingest Confluence pages into an org.")
    parser.add_argument(
        "--org", default=DEFAULT_ORG_NAME, help="Organization name to create/ingest into."
    )
    parser.add_argument(
        "--token",
        default=None,
        metavar="NAME",
        help="Which per-org Confluence credential to use, i.e. the <NAME> in "
        "CONFLUENCE_BASE_URL_<NAME> / _EMAIL_<NAME> / _TOKEN_<NAME> (case-insensitive). "
        "Omit to use the default CONFLUENCE_BASE_URL / _EMAIL / _TOKEN.",
    )
    args = parser.parse_args()
    org_name = args.org

    try:
        which = f"CONFLUENCE_*_{args.token.upper()}" if args.token else "CONFLUENCE_* (default)"
        print(f"Connecting to Confluence using {which} and preparing the store...")
        adapter = build_source_adapter("confluence", token_name=args.token)
        store = build_vector_store()
        apply_schema()

        refs = adapter.list_documents()
        print(f"Confluence credential can access {len(refs)} page(s):")
        for ref in refs:
            print(f"  - {ref.title}  ({ref.external_id})")
        if not refs:
            print("\nNo pages found. Check the credential's site/space permissions.")
            return 1

        org_id = store.create_organization(org_name)
        print(f"\nCreated organization '{org_name}': {org_id}")

        print("Ingesting (fetch -> chunk -> embed -> store)...")
        result = ingest_source(adapter, org_id, provider="confluence", store=store)

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
