"""Measure plaintext (A) vs encrypted (B) search on enc_bench.

    python -m evaluation.encbench.run

Writes evaluation/reports/encbench/results.json. No AI model is called: the
200 questions were embedded by embed.py, and timing stops when the passages
for the prompt are ready (the model's own seconds are the same for A and B).
"""

from __future__ import annotations

import json
import os
import random
import statistics
import subprocess
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from evaluation.encbench import BENCH_DB, CACHE, QUESTIONS

os.environ.update(DATABASE_URL=BENCH_DB, GRAPH_RETRIEVAL_ENABLED="false")

from app.config.settings import RetrievalSettings  # noqa: E402
from app.db.connection import close_pool  # noqa: E402
from app.rag.retrieval import HybridRetriever  # noqa: E402
from app.vectorstore.pgvector_store import PgVectorStore  # noqa: E402
from evaluation.encbench.crypto import KeyRing  # noqa: E402
from evaluation.encbench.store_enc import EncryptedStore  # noqa: E402

LATENCY_QUESTIONS = 50
ROUNDS = 10
USERS = (1, 5, 10, 25, 50)
LOAD_SECONDS = 20
KMS_MS = 100


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def summary(xs: list[float]) -> dict:
    return {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95), "p99": pct(xs, 99),
            "mean": statistics.fmean(xs)}


def postgres_cpu_seconds() -> float:
    """Cumulative CPU of every postgres process on this machine (ps TIME column)."""
    out = subprocess.run(["ps", "-axo", "time=,comm="], capture_output=True, text=True).stdout
    total = 0.0
    for line in out.splitlines():
        t, _, comm = line.strip().partition(" ")
        if "postgres" not in comm:
            continue
        parts = [float(x) for x in t.replace("-", ":").split(":")]
        while len(parts) < 3:
            parts.insert(0, 0.0)
        total += parts[-3] * 3600 + parts[-2] * 60 + parts[-1]
    return total


def main() -> None:
    orgs = json.loads((CACHE / "orgs.json").read_text())
    questions = [json.loads(l) for l in open(QUESTIONS)]
    qvecs = np.load(CACHE / "questions.npy")
    keyring = KeyRing.load(CACHE / "keys.json")
    a_store, b_store = PgVectorStore(), EncryptedStore(keyring)
    retrievers = {"A": HybridRetriever(a_store), "B": HybridRetriever(b_store)}
    settings = RetrievalSettings.from_env()
    pool = settings.candidate_pool
    results: dict = {"settings": {"candidate_pool": pool, "rrf_k": settings.rrf_k,
                                  "keyword_candidate_limit": a_store._settings.keyword_candidate_limit,
                                  "db_pool_max": a_store._settings.pool_max_size,
                                  "kms_simulated_ms": KMS_MS}}

    def ask(variant: str, org: str, i: int):
        return retrievers[variant].retrieve(org, questions[i]["question"], qvecs[i].tolist())

    # ---- accuracy: all 200 questions, tenant 1 ----------------------------
    org = orgs[0]
    docs_of = {}
    acc = {v: defaultdict(list) for v in "AB"}
    for i, q in enumerate(questions):
        expected = set(q["expected_doc_ids"])
        for v, store in (("A", a_store), ("B", b_store)):
            hits = ask(v, org, i).hits
            ext = _external_ids(store, org, [h.document_id for h in hits], docs_of)
            kw = store.keyword_search(org, q["question"], qvecs[i].tolist(), top_k=pool)
            kw_ext = _external_ids(store, org, [h.document_id for h in kw], docs_of)
            vec = store.query(org, qvecs[i].tolist(), top_k=pool)
            vec_ext = _external_ids(store, org, [h.document_id for h in vec], docs_of)
            # 30 questions name no document (answer not in the corpus, or a
            # broad overview): scored for A/B agreement only.
            found = expected & set(ext)
            n = len(expected)
            acc[v]["hit"].append(bool(found) if n else None)
            acc[v]["recall"].append(len(found) / n if n else None)
            acc[v]["keyword_recall"].append(len(expected & set(kw_ext)) / n if n else None)
            acc[v]["vector_recall"].append(len(expected & set(vec_ext)) / n if n else None)
            acc[v]["docs"].append(list(dict.fromkeys(ext)))
            acc[v]["kw_docs"].append(list(dict.fromkeys(kw_ext)))
            acc[v]["type"].append(q["question_type"])
    same_top = sum(a[:1] == b[:1] for a, b in zip(acc["A"]["docs"], acc["B"]["docs"]))
    jacc = [len(set(a) & set(b)) / max(1, len(set(a) | set(b))) for a, b in zip(acc["A"]["docs"], acc["B"]["docs"])]
    kw_jacc = [len(set(a) & set(b)) / max(1, len(set(a) | set(b))) for a, b in zip(acc["A"]["kw_docs"], acc["B"]["kw_docs"])]
    changed = [
        {"question_id": questions[i]["question_id"], "question": questions[i]["question"],
         "A_hit": acc["A"]["hit"][i], "B_hit": acc["B"]["hit"][i]}
        for i in range(len(questions))
        if acc["A"]["hit"][i] is not None and acc["A"]["hit"][i] != acc["B"]["hit"][i]
    ]
    by_type = {}
    for t in sorted(set(acc["A"]["type"])):
        idx = [i for i, x in enumerate(acc["A"]["type"]) if x == t and acc["A"]["hit"][i] is not None]
        if idx:
            by_type[t] = {v: {"n": len(idx), "hit": _mean(acc[v]["hit"][i] for i in idx),
                              "recall": _mean(acc[v]["recall"][i] for i in idx)} for v in "AB"}
    results["accuracy"] = {
        v: {"questions": len(questions),
            "scored_questions": sum(x is not None for x in acc[v]["hit"]),
            "hit_rate": _mean(acc[v]["hit"]),
            "doc_recall": _mean(acc[v]["recall"]),
            "keyword_leg_recall": _mean(acc[v]["keyword_recall"]),
            "vector_leg_recall": _mean(acc[v]["vector_recall"])}
        for v in "AB"
    }
    results["accuracy"]["same_top_document"] = same_top / len(questions)
    results["accuracy"]["final_docs_overlap"] = statistics.fmean(jacc)
    results["accuracy"]["keyword_docs_overlap"] = statistics.fmean(kw_jacc)
    results["accuracy"]["changed"] = changed
    results["accuracy"]["by_type"] = by_type
    print("accuracy", json.dumps({k: v for k, v in results["accuracy"].items() if k not in ("changed", "by_type")}), flush=True)

    # ---- single user: 50 questions x 10 rounds, A/B alternated ------------
    rng = random.Random(7)
    picks = _stratified(questions, LATENCY_QUESTIONS, rng)
    for i in picks:  # warm caches for both
        ask("A", org, i), ask("B", org, i)
    lat = {"A": [], "B": []}
    for r in range(ROUNDS):
        order = ("A", "B") if r % 2 == 0 else ("B", "A")
        for i in picks:
            for v in order:
                t0 = time.perf_counter()
                ask(v, org, i)
                lat[v].append((time.perf_counter() - t0) * 1000)
    results["single_user_ms"] = {v: summary(lat[v]) for v in "AB"}
    print("single", json.dumps(results["single_user_ms"]), flush=True)

    # ---- breakdown: each leg alone, sequential ----------------------------
    legs = {f"{v}_{leg}": [] for v in "AB" for leg in ("vector", "keyword")}
    b_crypto, b_codes = [], []
    for _ in range(3):
        for i in picks:
            q, vec = questions[i]["question"], qvecs[i].tolist()
            for v, store in (("A", a_store), ("B", b_store)):
                if v == "B":
                    b_store.reset()
                t0 = time.perf_counter()
                store.query(org, vec, top_k=pool)
                legs[f"{v}_vector"].append((time.perf_counter() - t0) * 1000)
                t0 = time.perf_counter()
                store.keyword_search(org, q, vec, top_k=pool)
                legs[f"{v}_keyword"].append((time.perf_counter() - t0) * 1000)
                if v == "B":
                    b_crypto.append(b_store.crypto_ms)
                    b_codes.append(b_store.codes_ms)
    results["legs_ms"] = {k: summary(x) for k, x in legs.items()}
    results["legs_ms"]["B_decrypt_per_question"] = summary(b_crypto)
    results["legs_ms"]["B_codes_per_question"] = summary(b_codes)
    print("legs", json.dumps({k: round(v["p50"], 2) for k, v in results["legs_ms"].items()}), flush=True)

    # ---- key not in memory -------------------------------------------------
    cold = {"B_cold_key_no_kms": [], "B_cold_key_kms_100ms": []}
    for name, kms in (("B_cold_key_no_kms", 0), ("B_cold_key_kms_100ms", KMS_MS)):
        keyring.kms_ms = kms
        for i in picks:
            keyring.forget()
            t0 = time.perf_counter()
            ask("B", org, i)
            cold[name].append((time.perf_counter() - t0) * 1000)
    keyring.kms_ms = 0
    results["cold_key_ms"] = {k: summary(x) for k, x in cold.items()}
    print("cold", json.dumps({k: round(v["p50"], 1) for k, v in results["cold_key_ms"].items()}), flush=True)

    # ---- many users at once, across the 10 tenants -------------------------
    results["load"] = {}
    for users in USERS:
        for v in (("A", "B") if users % 2 else ("B", "A")):
            results["load"][f"{v}_{users}"] = _load(ask, v, users, orgs, picks)
            print("load", v, users, json.dumps({k: round(x, 1) for k, x in results["load"][f"{v}_{users}"].items()}), flush=True)

    results["kms_calls"] = keyring.kms_calls
    (CACHE / "results.json").write_text(json.dumps(results, indent=2))
    close_pool()
    print("wrote", CACHE / "results.json")


def _mean(xs) -> float:
    return statistics.fmean(float(x) for x in xs if x is not None)


def _external_ids(store, org, document_ids, cache):
    missing = [d for d in document_ids if d not in cache]
    if missing:
        from app.db.connection import get_connection

        with get_connection(org_id=org) as conn:
            for did, ext in conn.execute(
                "SELECT id::text, source_external_id FROM documents WHERE id = ANY(%s::uuid[])", (missing,)
            ):
                cache[did] = ext
    return [cache[d] for d in document_ids]


def _stratified(questions, n, rng):
    by_type = defaultdict(list)
    for i, q in enumerate(questions):
        by_type[(q["question_type"], tuple(q["source_types"]))].append(i)
    groups = [rng.sample(v, len(v)) for v in by_type.values()]
    picks = []
    while len(picks) < n:
        for g in groups:
            if g and len(picks) < n:
                picks.append(g.pop())
    return picks


def _load(ask, variant, users, orgs, picks) -> dict:
    lat: list[float] = []
    errors = [0]
    lock = threading.Lock()
    stop = time.monotonic() + LOAD_SECONDS
    cpu0 = postgres_cpu_seconds()

    def user(seed):
        r = random.Random(seed)
        while time.monotonic() < stop:
            org, i = r.choice(orgs), r.choice(picks)
            t0 = time.perf_counter()
            try:
                ask(variant, org, i)
            except Exception:  # noqa: BLE001 - counted, not raised
                with lock:
                    errors[0] += 1
                continue
            with lock:
                lat.append((time.perf_counter() - t0) * 1000)

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=users) as ex:
        list(ex.map(user, range(users)))
    wall = time.monotonic() - t0
    s = summary(lat)
    return {"questions_per_s": len(lat) / wall, "p50": s["p50"], "p95": s["p95"], "p99": s["p99"],
            "errors": errors[0], "postgres_cpu_cores": (postgres_cpu_seconds() - cpu0) / wall}


if __name__ == "__main__":
    main()
