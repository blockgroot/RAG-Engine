"""Build enc_bench: the same 10 tenants twice, plaintext (A) and encrypted (B).

A is the app's own `chunks` table (schema.sql as on main: one partition per
org, halfvec, HNSW + the `content_tsv` GIN index, row-level security).
B is `chunks_enc`, laid out the same way (partition per org, HNSW, the same
row-level policy) but holding AES-GCM text and a GIN-indexed `word_ints`
array (word codes as 32-bit integers) instead of readable text and `content_tsv`.

Every tenant is a copy of the Benchmark 2 pilot (1,374 documents, 11,060
chunks) with the vectors from `embed.py`, so A and B differ ONLY in how the
text is stored. Each tenant has its own key, so its ciphertext and codes differ.

    python -m evaluation.encbench.build
"""

from __future__ import annotations

import json
import os
import time
import uuid

import numpy as np
import psycopg
from pgvector import HalfVector
from pgvector.psycopg import register_vector

from evaluation.encbench import BENCH_DB, CACHE, TENANTS
from evaluation.encbench.crypto import KeyRing, lexemes

B_SCHEMA = """
CREATE EXTENSION IF NOT EXISTS intarray;
CREATE SCHEMA IF NOT EXISTS enc_partitions;
CREATE TABLE chunks_enc (
    id            UUID NOT NULL DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    document_id   UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index   INT  NOT NULL,
    content       BYTEA NOT NULL,
    word_ints     INT[] NOT NULL,
    embedding     halfvec(1024) NOT NULL,
    workspace_id  UUID,
    injection_score REAL,
    PRIMARY KEY (org_id, id)
) PARTITION BY LIST (org_id);
CREATE INDEX idx_chunks_enc_embedding ON chunks_enc USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX idx_chunks_enc_codes ON chunks_enc USING gin (word_ints gin__int_ops);
ALTER TABLE chunks_enc ENABLE ROW LEVEL SECURITY;
CREATE POLICY chunks_enc_one_org ON chunks_enc TO handbook_app
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid
           OR current_setting('app.all_orgs', true) = 'on');
GRANT SELECT, INSERT, UPDATE, DELETE ON chunks_enc TO handbook_app;
"""


def main() -> None:
    chunks = [json.loads(l) for l in open(CACHE / "chunks.jsonl")]
    vectors = np.load(CACHE / "chunks.npy")
    assert len(chunks) == len(vectors)

    with psycopg.connect("postgresql://localhost/postgres", autocommit=True) as admin:
        admin.execute("DROP DATABASE IF EXISTS enc_bench")
        admin.execute("CREATE DATABASE enc_bench")
    os.environ["DATABASE_URL"] = BENCH_DB
    from app.db.migrate import apply_schema

    apply_schema()

    keyring = KeyRing(os.urandom(32))
    stats: dict = {"chunks_per_tenant": len(chunks), "tenants": TENANTS}
    with psycopg.connect(BENCH_DB) as conn:
        register_vector(conn)
        conn.execute(B_SCHEMA)
        orgs = []
        for t in range(1, TENANTS + 1):
            org = str(conn.execute("INSERT INTO organizations (name) VALUES (%s) RETURNING id",
                                   (f"enc-t{t:02d}",)).fetchone()[0])
            conn.execute(
                f"CREATE TABLE enc_partitions.org_{org.replace('-', '')} "
                f"PARTITION OF chunks_enc FOR VALUES IN ('{org}')"
            )
            keyring.create(org)
            orgs.append(org)
        conn.commit()

        docs = {}
        for c in chunks:
            docs.setdefault(c["doc"], c)
        doc_ids: dict[tuple[str, str], str] = {}
        for org in orgs:
            with conn.cursor().copy(
                "COPY documents (id, org_id, title, source_uri, source_provider, source_external_id) FROM STDIN"
            ) as cp:
                for ext, c in docs.items():
                    did = uuid.uuid4()
                    doc_ids[(org, ext)] = did
                    cp.write_row((did, org, c["title"] or "Untitled", c["uri"], c["provider"], ext))
        conn.commit()

        # Precompute lexemes once: they do not depend on the key, and timing
        # them per tenant would only re-measure the stemmer cache.
        t0 = time.perf_counter()
        lex = [sorted(lexemes(c["content"])) for c in chunks]
        stats["lexemes_s_per_chunk_cold"] = (time.perf_counter() - t0) / len(chunks)

        a_insert = b_prepare = b_insert = 0.0
        halfs = [HalfVector(v.tolist()) for v in vectors]
        for org in orgs:
            oid = uuid.UUID(org)
            t0 = time.perf_counter()
            with conn.cursor().copy(
                "COPY chunks (org_id, document_id, chunk_index, content, embedding) FROM STDIN WITH (FORMAT BINARY)"
            ) as cp:
                cp.set_types(["uuid", "uuid", "int4", "text", "halfvec"])
                for c, h in zip(chunks, halfs):
                    cp.write_row((oid, doc_ids[(org, c["doc"])], c["idx"], c["content"], h))
            conn.commit()
            a_insert += time.perf_counter() - t0

            keys = keyring.keys(org)
            t0 = time.perf_counter()
            rows = [
                (oid, doc_ids[(org, c["doc"])], c["idx"], keys.encrypt(c["content"]),
                 sorted({int.from_bytes(bytes.fromhex(keys.code(w)[:8]), "big", signed=True) for w in words}), h)
                for c, words, h in zip(chunks, lex, halfs)
            ]
            b_prepare += time.perf_counter() - t0

            t0 = time.perf_counter()
            with conn.cursor().copy(
                "COPY chunks_enc (org_id, document_id, chunk_index, content, word_ints, embedding) "
                "FROM STDIN WITH (FORMAT BINARY)"
            ) as cp:
                cp.set_types(["uuid", "uuid", "int4", "bytea", "int4[]", "halfvec"])
                for r in rows:
                    cp.write_row(r)
            conn.commit()
            b_insert += time.perf_counter() - t0
            print(f"tenant {org}: A {a_insert:.0f}s, B prepare {b_prepare:.0f}s, B insert {b_insert:.0f}s", flush=True)

        conn.execute("ANALYZE chunks; ANALYZE chunks_enc; ANALYZE documents")
        conn.commit()
        n = len(chunks) * TENANTS
        stats.update(
            a_insert_s=a_insert, b_prepare_s=b_prepare, b_insert_s=b_insert,
            a_insert_ms_per_chunk=a_insert / n * 1000,
            b_prepare_ms_per_chunk=b_prepare / n * 1000,
            b_insert_ms_per_chunk=b_insert / n * 1000,
            avg_codes_per_chunk=sum(map(len, lex)) / len(lex),
        )
        stats["sizes"] = sizes(conn)

    keyring.save(CACHE / "keys.json")
    (CACHE / "orgs.json").write_text(json.dumps(orgs))
    (CACHE / "build.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


def sizes(conn) -> dict:
    """Bytes per table, split into data and each index, summed over partitions."""
    out = {}
    for parent, indexes in (("chunks", ("embedding", "content_tsv", "pkey")),
                            ("chunks_enc", ("embedding", "word_ints", "pkey"))):
        parts = [r[0] for r in conn.execute(
            "SELECT relid::regclass::text FROM pg_partition_tree(%s) WHERE isleaf", (parent,))]
        total = heap = 0
        idx = {k: 0 for k in indexes}
        for p in parts:
            total += conn.execute("SELECT pg_total_relation_size(%s)", (p,)).fetchone()[0]
            heap += conn.execute("SELECT pg_table_size(%s)", (p,)).fetchone()[0]
            for name, size in conn.execute(
                "SELECT indexrelid::regclass::text, pg_relation_size(indexrelid) FROM pg_index WHERE indrelid = %s::regclass",
                (p,),
            ):
                for k in indexes:
                    if k in name:
                        idx[k] += size
                        break
        out[parent] = {"total": total, "table": heap, "indexes": idx}
    return out


if __name__ == "__main__":
    main()
