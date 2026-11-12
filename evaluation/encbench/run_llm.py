"""End-to-end answers on A and B: the full Ask pipeline, with a real LLM.

    LLM_MODEL=... LLM_API_KEY=... LLM_BASE_URL=... python -m evaluation.encbench.run_llm

The same 20 questions are answered on the plaintext (A) and encrypted (B)
stores, alternating, through `build_rag_pipeline` as the app builds it, with
the answer cache OFF so every answer really runs. Memory, web search,
recovery and tone are off, as in the test suite, and no reranker (the local
one is not downloaded here); all of that is identical for A and B.

Also measures the one-time cost per company of the spelling normalizer's
vocabulary (every chunk's text; on B, every chunk decrypted).

Writes evaluation/reports/encbench/results_llm.json.
"""

from __future__ import annotations

import json
import os
import random
import time

from evaluation.encbench import BENCH_DB, CACHE, QUESTIONS

os.environ.update(DATABASE_URL=BENCH_DB, QUERY_CACHE_ENABLED="false",
                  EMBEDDING_BACKEND="local", EMBEDDING_MODEL="BAAI/bge-m3",
                  EMBEDDING_API_KEY="", EMBEDDING_BASE_URL="")

from app.config.settings import RecoverySettings, ToneSettings  # noqa: E402
from app.db.connection import close_pool  # noqa: E402
from app.rag.factory import build_rag_pipeline  # noqa: E402
from app.rag.retrieval import HybridRetriever  # noqa: E402
from app.vectorstore.pgvector_store import PgVectorStore  # noqa: E402
from evaluation.encbench.crypto import KeyRing  # noqa: E402
from evaluation.encbench.run import _stratified, summary  # noqa: E402
from evaluation.encbench.store_enc import EncryptedStore  # noqa: E402

N_QUESTIONS = 20


def main() -> None:
    if not os.getenv("LLM_MODEL"):
        raise SystemExit("Set LLM_MODEL, LLM_API_KEY and LLM_BASE_URL first.")
    orgs = json.loads((CACHE / "orgs.json").read_text())
    questions = [json.loads(l) for l in open(QUESTIONS)]
    keyring = KeyRing.load(CACHE / "keys.json")
    stores = {"A": PgVectorStore(), "B": EncryptedStore(keyring)}

    results: dict = {"vocab_once_per_company_ms": {}}
    for v, store in stores.items():
        t0 = time.perf_counter()
        texts = store.list_chunk_texts(orgs[1])
        results["vocab_once_per_company_ms"][v] = {
            "ms": (time.perf_counter() - t0) * 1000, "chunks": len(texts)}

    pipelines = {
        v: build_rag_pipeline(
            store=store, retriever=HybridRetriever(store), memory=None, web_search=None,
            recovery_settings=RecoverySettings(enabled=False), tone_settings=ToneSettings(enabled=False),
        )
        for v, store in stores.items()
    }
    org = orgs[0]
    picks = _stratified(questions, N_QUESTIONS, random.Random(11))
    pipelines["A"].answer("warm up", org), pipelines["B"].answer("warm up", org)

    lat = {"A": [], "B": []}
    rows = []
    for n, i in enumerate(picks):
        q = questions[i]["question"]
        row = {"question_id": questions[i]["question_id"]}
        for v in (("A", "B") if n % 2 == 0 else ("B", "A")):
            t0 = time.perf_counter()
            result = pipelines[v].answer(q, org)
            ms = (time.perf_counter() - t0) * 1000
            lat[v].append(ms)
            row[v] = {"ms": ms, "answered": bool(result.answer), "chars": len(result.answer or "")}
        rows.append(row)
        print(n + 1, json.dumps({v: round(row[v]["ms"]) for v in "AB"}), flush=True)

    results["answer_ms"] = {v: summary(lat[v]) for v in "AB"}
    results["questions"] = rows
    (CACHE / "results_llm.json").write_text(json.dumps(results, indent=2))
    close_pool()
    print(json.dumps({k: v for k, v in results.items() if k != "questions"}, indent=2))


if __name__ == "__main__":
    main()
