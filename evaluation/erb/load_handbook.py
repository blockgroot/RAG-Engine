"""Load the frozen pilot into the Handbook benchmark database (plan §6.2, §8).

Same steps as production ingest minus the per-chunk AI context line (off for
the benchmark, as benchmarks.md requires): preprocess -> chunk -> embed (Jina)
-> ``upsert_source_document`` with ``external_id = dsid``, so an answer's
documents map straight back to the benchmark's gold ids.

``--production-ingest`` adds the two enrichment steps production runs by default
and the plain load skips: the AI context line (+ hypothetical questions) per
chunk from ``contextualize_chunks`` and the keyword line. Load it into its own
org (``BENCH_ORG``) so the plain corpus stays comparable; run_bench reads the
same variable.

Idempotent: a re-run skips documents already stored, so a crash never spends
the Jina balance twice.

Run: .venv/bin/python -m evaluation.erb.load_handbook --data ~/Desktop/bench1-data [--limit 100]
     BENCH_ORG=bench2-erb-contextual ... --production-ingest   (aux LLM through a proxy)
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from app.db import get_connection
from app.embeddings import build_embedding_provider
from app.ingestion.chunking import chunk_text
from app.ingestion.preprocessing import preprocess
from app.vectorstore import build_vector_store

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


def _enricher():
    """Production's ingest enrichment, in production's order (ingestion/pipeline.py)."""
    from app.config.settings import ContextualSettings, KeywordExtractionSettings
    from app.ingestion.contextualize import contextualize_chunks
    from app.ingestion.keywords import append_keyword_line
    from app.llm.factory import build_aux_llm_provider

    contextual, keywords = ContextualSettings.from_env(), KeywordExtractionSettings.from_env()
    llm = build_aux_llm_provider()

    def enrich(clean: str, raw_chunks: list[str], org_id: str) -> list[str]:
        chunks = raw_chunks
        if contextual.enabled and len(raw_chunks) <= contextual.max_chunks:
            chunks = contextualize_chunks(llm, clean, raw_chunks, org_id=org_id,
                                          concurrency=contextual.concurrency,
                                          hypothetical_questions=contextual.hypothetical_questions)
        if keywords.enabled:
            chunks = [append_keyword_line(s, r, keywords.top_n) for s, r in zip(chunks, raw_chunks)]
        return chunks

    return enrich


def bench_org(store) -> str:
    # Read at call time: run_bench applies --set BENCH_ORG=... after import.
    name = os.getenv("BENCH_ORG") or ORG_NAME
    for org in store.list_organizations():
        if org.name == name:
            return org.id
    return store.create_organization(name)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--only-docs", type=Path, default=None,
                    help="file of dsids, one per line: load just these (a targeted re-test)")
    ap.add_argument("--production-ingest", action="store_true",
                    help="add production's AI context line, hypothetical questions and keyword line")
    args = ap.parse_args()
    # Only here, not on import: run_bench imports this module after applying a
    # variant's --set values, and an import-time reload silently reverted them.
    load_dotenv(".env.bench", override=True)
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

    enrich = _enricher() if args.production_ingest else None
    ids = manifest["gold_doc_ids"] + manifest["noise_doc_ids"]
    if args.only_docs:
        wanted = set(args.only_docs.read_text().split())
        ids = [d for d in ids if d in wanted]
    todo = [d for d in ids if d not in done][: args.limit]
    t0, n_chunks = time.time(), 0
    for i, dsid in enumerate(todo, 1):
        text = read_doc(args.data, dsid)
        source = manifest["doc_paths"][dsid].split("/")[0]
        title = text.strip().splitlines()[0][:300] if text.strip() else dsid
        clean = preprocess(text)
        chunks = chunk_text(clean)
        if not chunks:
            continue
        if enrich:
            chunks = enrich(clean, chunks, org_id)
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
