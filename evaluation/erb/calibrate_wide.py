"""Choose the wide-read settings from data, not by hand.

Step 1 (``--collect``): for every question in a split, run Handbook's real
retriever with the whole candidate pool kept, and record each passage's
reranker score and benchmark document id. Embedding + reranker only: no
answer-model call.

Step 2 (default): for each candidate ``RAG_WIDE_DOC_RATIO``, simulate
``retrieval._spread`` on those scores and report, per question type:
- how often a one-document question stays narrow (it should: same cost as today),
- how many of the right documents a many-document question reaches,
- passages read per question (the token cost).
Pick the ratio where many-document coverage stops rising and narrow questions
still stay narrow.

Run:
  BENCH_DIR=evaluation/reports/bench2-followup .venv/bin/python -m evaluation.erb.calibrate_wide --collect --split dev --pool 40
  BENCH_DIR=evaluation/reports/bench2-followup .venv/bin/python -m evaluation.erb.calibrate_wide
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

BENCH = Path(os.getenv("BENCH_DIR", "evaluation/reports/bench1"))
OUT = BENCH / "calib_pool.jsonl"
DATA = Path("~/Desktop/bench1-data").expanduser()


def collect(split: str, pool: int) -> None:
    load_dotenv(".env.bench", override=True)
    from app.config.settings import RagSettings, RetrievalSettings
    from app.embeddings import build_embedding_provider
    from app.rag.retrieval import HybridRetriever
    from app.reranker import build_reranker
    from app.vectorstore import build_vector_store

    from .load_handbook import bench_org
    from .run_bench import _dsid_map, _questions

    store = build_vector_store()
    retriever = HybridRetriever(
        store=store,
        reranker=build_reranker(),
        settings=replace(RetrievalSettings.from_env(), candidate_pool=pool, rerank_min_ratio=0.0),
        rag_settings=replace(RagSettings.from_env(), top_k=pool, wide_max_hits=0),
    )
    embedder, org = build_embedding_provider(), bench_org(store)
    done = {json.loads(line)["question_id"] for line in OUT.open()} if OUT.exists() else set()
    todo = [q for q in _questions(DATA, split) if q["question_id"] not in done]
    for i, q in enumerate(todo, 1):
        hits = retriever.retrieve(org, q["question"], embedder.embed([q["question"]])[0]).hits
        row = {"question_id": q["question_id"], "type": q["question_type"], "split": split,
               "gold": q["expected_doc_ids"], "pool": pool,
               "hits": [[d, h.rerank_score] for d, h in zip(_dsid_map([h.document_id for h in hits]), hits)]}
        with OUT.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{i}/{len(todo)} {q['question_id']} {len(hits)} passages", flush=True)
        time.sleep(6)  # Jina's free tier limits reranked tokens per minute


def _select(hits: list, top_k: int, max_hits: int, ratio: float, per_doc: int) -> list:
    """``retrieval._spread`` on recorded (doc, score) pairs, best-first."""
    from types import SimpleNamespace

    from app.rag.retrieval import _spread

    chunks = [SimpleNamespace(document_id=d, rerank_score=s, i=i) for i, (d, s) in enumerate(hits)]
    return [(c.document_id, c.rerank_score) for c in _spread(chunks, top_k, max_hits, ratio, per_doc)]


def analyse(top_k: int, max_hits: int, per_doc: int, split: str | None) -> None:
    rows = [json.loads(line) for line in OUT.open()]
    rows = [r for r in rows if r["gold"] and r["hits"] and (split is None or r["split"] == split)]
    single = [r for r in rows if len(r["gold"]) == 1]
    multi = [r for r in rows if len(r["gold"]) >= 3]
    print(f"{len(rows)} questions with known documents: {len(single)} one-document, {len(multi)} three-or-more")
    base = {r["question_id"]: {d for d, _ in r["hits"][:top_k]} for r in rows}
    print(f"\ntop_k={top_k}, max_hits={max_hits}, per_doc={per_doc}")
    print("ratio | one-doc stays narrow | many-doc: right docs reached (today -> wide) | passages/q (all)")
    for ratio in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        narrow = sum(len(_select(r["hits"], top_k, max_hits, ratio, per_doc)) <= top_k for r in single)
        cov_now = cov_wide = 0.0
        for r in multi:
            gold = set(r["gold"])
            cov_now += len(base[r["question_id"]] & gold) / len(gold)
            cov_wide += len({d for d, _ in _select(r["hits"], top_k, max_hits, ratio, per_doc)} & gold) / len(gold)
        passages = sum(len(_select(r["hits"], top_k, max_hits, ratio, per_doc)) for r in rows) / len(rows)
        print(f"{ratio:.1f} | {narrow}/{len(single)} | {100 * cov_now / len(multi):.0f}% -> "
              f"{100 * cov_wide / len(multi):.0f}% | {passages:.1f}")
    # Where the right documents sit in the reranked pool: is the pool big enough?
    ranks = defaultdict(int)
    for r in multi:
        seen: list[str] = []
        for d, _ in r["hits"]:
            if d not in seen:
                seen.append(d)
        for g in r["gold"]:
            pos = seen.index(g) + 1 if g in seen else None
            ranks["missing" if pos is None else "1-5" if pos <= 5 else "6-10" if pos <= 10 else "11+"] += 1
    print("\nmany-doc questions, rank of each right document among distinct documents:", dict(ranks))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--split", default=None, choices=["dev", "test"], help="collect: required; analyse: default both")
    ap.add_argument("--pool", type=int, default=40)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--max-hits", type=int, default=10)
    ap.add_argument("--per-doc", type=int, default=2)
    args = ap.parse_args()
    if args.collect:
        collect(args.split or "dev", args.pool)
    else:
        analyse(args.top_k, args.max_hits, args.per_doc, args.split)


if __name__ == "__main__":
    main()
