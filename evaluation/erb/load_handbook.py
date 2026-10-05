"""Load the frozen pilot into the Handbook benchmark database (plan §6.2, §8).

Same steps as production ingest minus the per-chunk AI context line (off for
the benchmark, as benchmarks.md requires): preprocess -> chunk -> embed (Jina)
-> ``upsert_source_document`` with ``external_id = dsid``, so an answer's
documents map straight back to the benchmark's gold ids.

Idempotent: a re-run skips documents already stored, so a crash never spends
the Jina balance twice.

Run: .venv/bin/python -m evaluation.erb.load_handbook --data ~/Desktop/bench1-data [--limit 100]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env.bench", override=True)

from app.db import get_connection  # noqa: E402
from app.embeddings import build_embedding_provider  # noqa: E402
from app.ingestion.chunking import chunk_text  # noqa: E402
from app.ingestion.preprocessing import preprocess  # noqa: E402
from app.vectorstore import build_vector_store  # noqa: E402

ORG_NAME = "bench1-erb-pilot"
MANIFEST = Path("evaluation/reports/bench1/manifest.json")
# Our four matching connectors keep their provider name; the rest share one.
PROVIDERS = {"slack": "slack", "linear": "linear", "google_drive": "google"}


def read_doc(data: Path, dsid: str) -> str:
    """The pilot text exactly as BOTH systems receive it. NUL bytes are dropped
    (Postgres rejects them); every loader must read through here."""
    return (data.expanduser() / "pilot_docs" / f"{dsid}.txt").read_text().replace("\x00", "")


def _embed_waiting(embedder, chunks: list[str]) -> list[list[float]]:
    """Jina's free tier allows 100k tokens/min; wait it out instead of dying."""
    for attempt in range(10):
        try:
            return embedder.embed(chunks)
        except Exception as exc:  # noqa: BLE001 - only the rate limit is retried
            if "429" not in str(exc) or attempt == 9:
                raise
            time.sleep(30)
    raise AssertionError("unreachable")


def bench_org(store) -> str:
    for org in store.list_organizations():
        if org.name == ORG_NAME:
            return org.id
    return store.create_organization(ORG_NAME)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    assert "localhost" in os.environ["DATABASE_URL"], "benchmark must never write to the remote database"

    from app.db.migrate import apply_schema

    apply_schema()
    manifest = json.loads(MANIFEST.read_text())
    store, embedder = build_vector_store(), build_embedding_provider()
    org_id = bench_org(store)
    with get_connection() as conn:
        done = {r[0] for r in conn.execute(
            "SELECT source_external_id FROM documents WHERE org_id = %s::uuid", (org_id,)
        ).fetchall()}

    ids = manifest["gold_doc_ids"] + manifest["noise_doc_ids"]
    todo = [d for d in ids if d not in done][: args.limit]
    t0, n_chunks = time.time(), 0
    for i, dsid in enumerate(todo, 1):
        text = read_doc(args.data, dsid)
        source = manifest["doc_paths"][dsid].split("/")[0]
        title = text.strip().splitlines()[0][:300] if text.strip() else dsid
        chunks = chunk_text(preprocess(text))
        if not chunks:
            continue
        store.upsert_source_document(
            org_id,
            provider=PROVIDERS.get(source, "erb_other"),
            external_id=dsid,
            title=title,
            chunks=chunks,
            embeddings=_embed_waiting(embedder, chunks),
            tags=[f"erb:{source}"],
        )
        n_chunks += len(chunks)
        if i % 50 == 0:
            print(f"{i}/{len(todo)} docs, {n_chunks} chunks, {time.time() - t0:.0f}s", flush=True)
    print(f"done: {len(todo)} new docs, {n_chunks} chunks, {time.time() - t0:.0f}s, org={org_id}")


if __name__ == "__main__":
    main()
