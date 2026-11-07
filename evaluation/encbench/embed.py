"""Embed the Benchmark 2 pilot corpus and its 200 questions ONCE, locally.

Both the plaintext and the encrypted variant load these same vectors, so the
comparison measures storage and search, never the embedding model. The pilot's
stored vectors are Jina's, so the questions could not be embedded to match them
without a Jina key; BGE-M3 runs on this machine for both sides instead.

    python -m evaluation.encbench.embed
"""

from __future__ import annotations

import json
import os
import sys
import time

import numpy as np
import psycopg

from evaluation.encbench import CACHE, QUESTIONS, SOURCE_DB, SOURCE_ORG

os.environ.update(EMBEDDING_BACKEND="local", EMBEDDING_MODEL="BAAI/bge-m3",
                  EMBEDDING_API_KEY="", EMBEDDING_BASE_URL="")

BATCH = 64


def main() -> None:
    from app.embeddings import build_embedding_provider

    CACHE.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(SOURCE_DB) as conn:
        rows = conn.execute(
            """
            SELECT d.source_external_id, d.title, d.source_provider, d.source_uri,
                   c.chunk_index, c.content
              FROM chunks c
              JOIN documents d ON d.id = c.document_id
              JOIN organizations o ON o.id = c.org_id
             WHERE o.name = %s
             ORDER BY d.source_external_id, c.chunk_index
            """,
            (SOURCE_ORG,),
        ).fetchall()
    with open(CACHE / "chunks.jsonl", "w") as f:
        for ext, title, provider, uri, idx, content in rows:
            f.write(json.dumps({"doc": ext, "title": title, "provider": provider,
                                "uri": uri, "idx": idx, "content": content}) + "\n")

    embedder = build_embedding_provider()
    questions = [json.loads(l) for l in open(QUESTIONS)]
    qvec = embedder.embed([q["question"] for q in questions])
    np.save(CACHE / "questions.npy", np.asarray(qvec, dtype=np.float32))

    out = np.zeros((len(rows), 1024), dtype=np.float32)
    start = time.time()
    for i in range(0, len(rows), BATCH):
        out[i:i + BATCH] = embedder.embed([r[5] for r in rows[i:i + BATCH]])
        done = i + BATCH
        print(f"{min(done, len(rows))}/{len(rows)} chunks, {time.time() - start:.0f}s",
              file=sys.stderr, flush=True)
    np.save(CACHE / "chunks.npy", out)
    print(f"embedded {len(rows)} chunks and {len(questions)} questions")


if __name__ == "__main__":
    main()
