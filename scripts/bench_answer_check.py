#!/usr/bin/env python3
"""Benchmark answer checkers on RAGTruth-QA: which one catches invented claims?

Everything runs REMOTELY — the dataset comes from the HF datasets server and
each checker is an HTTP endpoint — so nothing is loaded on this machine.

Checkers (``--checkers``, comma-separated):
  lettuce       OUR endpoint (RAG_AUDIT_LETTUCE_URL), via app.rag.audit.lettuce_score
  jev           TypeSafe's hosted Jev (RAG_AUDIT_JEV_API_KEY), via app.rag.audit.jev_score
  lettuce_demo  the public LettuceDetect demo Space (public data ONLY — never tenant data)
  laya_demo     the public Laya demo Space; one 3-way choice per sentence per passage
  llm           today's audit: build_audit_prompt on the configured LLM

Reports AUROC (threshold-free), the best balanced accuracy and its cutoff, and
false positives at the cutoff — a false positive is a GOOD answer replaced by
"I don't know", the cost that kept the LLM audit switched off. Results append
to ``--out`` and a rerun resumes, so a Space hiccup costs nothing.

Measured 2026-09-25, 30 grounded + 30 hallucinated (seed 7):
  lettuce_demo AUROC 0.893, bacc 0.883 @0.61, 1/30 good answers rejected
  laya_demo    AUROC 0.783, bacc 0.767 @0.90 (21/30 rejected at 0.5)
  llm (gemini-3.1-flash-lite) AUROC 0.767, bacc 0.767, 12/30 rejected

Usage:
    .venv/bin/python scripts/bench_answer_check.py --n 30
    .venv/bin/python scripts/bench_answer_check.py --n 50 --checkers lettuce,llm
"""

from __future__ import annotations

import argparse
import json
import random
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

DATASET = "flowaicom/RAGTruth_test"  # score 1 = grounded, 0 = hallucinated (dataset card)


def fetch_examples(n: int, seed: int) -> list[dict]:
    rows = []
    for offset in range(0, 900, 100):
        body = httpx.get(
            "https://datasets-server.huggingface.co/rows",
            params=dict(dataset=DATASET, config="default", split="qa", offset=offset, length=100),
            timeout=60,
        ).json()
        rows += [r["row"] for r in body["rows"]]
    rng = random.Random(seed)
    good = [r for r in rows if int(r["score"]) == 1]
    bad = [r for r in rows if int(r["score"]) == 0]
    out = []
    for r in rng.sample(good, n) + rng.sample(bad, n):
        info = json.loads(r["source_info"])
        passages = [p.strip() for p in re.split(r"passage \d+:", info["passages"]) if p.strip()]
        out.append(dict(id=str(r["id"]), label=1 - int(r["score"]),  # label 1 = hallucinated
                        question=info["question"], passages=passages, answer=r["response"]))
    return out


def gradio(space: str, api: str, data: list, tries: int = 4):
    base = f"https://{space}.hf.space/gradio_api/call/{api}"
    for attempt in range(tries):
        try:
            event_id = httpx.post(base, json={"data": data}, timeout=60).json()["event_id"]
            text = httpx.get(f"{base}/{event_id}", timeout=180).text
            for block in text.split("\n\n"):
                if block.startswith("event: complete"):
                    return json.loads(block.split("data: ", 1)[1])
            raise RuntimeError(text[-300:])
        except Exception as exc:  # noqa: BLE001 - retried, then re-raised
            last = exc
            time.sleep(5 * (attempt + 1))
    raise last


def sentences(text: str) -> list[str]:
    s = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if len(x.strip()) > 3]
    return s[:12] or [text]


def check_lettuce(ex):
    from app.config.settings import AuditSettings
    from app.rag.audit import lettuce_score

    settings = AuditSettings.from_env()
    if not settings.lettuce_url:
        raise RuntimeError("set RAG_AUDIT_LETTUCE_URL (and _TOKEN) to your private Space")
    scored = lettuce_score(settings, ex["question"], ex["passages"], ex["answer"])
    if scored is None:
        raise RuntimeError("endpoint unavailable or context over budget")
    return scored[0]


def check_jev(ex):
    from app.config.settings import AuditSettings
    from app.rag.audit import jev_score

    settings = AuditSettings.from_env()
    if not settings.jev_api_key:
        raise RuntimeError("set RAG_AUDIT_JEV_API_KEY")
    scored = jev_score(settings, ex["question"], ex["passages"], ex["answer"])
    if scored is None:
        raise RuntimeError("Jev unavailable or context over budget")
    return 1.0 - scored[0]  # p_halluc = 1 - weakest sentence's P(supported)


def check_lettuce_demo(ex):
    out = gradio("tonic-hallucination-test", "evaluate_hallucination",
                 ["\n\n".join(ex["passages"]), ex["question"], ex["answer"]])
    return max((float(c) for c in re.findall(r"conf: ([0-9.]+)", json.dumps(out[2]))), default=0.0)


_LAYA_Q = json.dumps({"y": {"type": "choice", "instructions": "How does the passage relate to the claim?",
                            "criteria": {"supported": "the passage states this",
                                         "contradicted": "the passage says otherwise",
                                         "not_mentioned": "the passage does not say this"}}})


def check_laya_demo(ex):
    # 512-token window: a sentence counts as supported by its best passage; the
    # answer is as grounded as its least-supported sentence. This phrasing (JSON
    # state + 3-way choice) beat a bare noul question by hand.
    sents = sentences(ex["answer"])
    best = [0.0] * len(sents)
    for p in ex["passages"]:
        for i, s in enumerate(sents):
            out = gradio("convaiinnovations-laya-demo", "run_playground",
                         [json.dumps({"passage": p[:1500], "claim": s}), _LAYA_Q])
            best[i] = max(best[i], float(json.loads(out[1])["answers"]["y"]["probabilities"]["supported"]))
    return 1.0 - min(best)


def check_llm(ex, _cache={}):
    from app.llm import build_llm_provider
    from app.rag.audit import parse_audit_verdict
    from app.rag.prompts import build_audit_prompt

    llm = _cache.setdefault("llm", build_llm_provider())
    verdict = parse_audit_verdict(
        llm.generate(build_audit_prompt(ex["question"], ex["passages"], ex["answer"]), max_tokens=120)
    )
    if verdict.grounded is None:
        raise RuntimeError("unparseable verdict")
    return 0.0 if verdict.grounded else 1.0


CHECKERS = {"lettuce": check_lettuce, "jev": check_jev, "lettuce_demo": check_lettuce_demo,
            "laya_demo": check_laya_demo, "llm": check_llm}


def auroc(ys, ps):
    pos = [p for y, p in zip(ys, ps) if y]
    neg = [p for y, p in zip(ys, ps) if not y]
    return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))


def best_cutoff(ys, ps):
    best = (0.0, 1.01, 0)
    for t in sorted(set(ps)) + [1.01]:
        pred = [p >= t for p in ps]
        tpr = sum(p and y for p, y in zip(pred, ys)) / max(1, sum(ys))
        tnr = sum(not p and not y for p, y in zip(pred, ys)) / max(1, len(ys) - sum(ys))
        fp = sum(p and not y for p, y in zip(pred, ys))
        best = max(best, ((tpr + tnr) / 2, t, fp), key=lambda b: b[0])
    return best


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="examples PER CLASS")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--checkers", default="lettuce_demo,laya_demo,llm")
    ap.add_argument("--out", default="bench_answer_check.jsonl")
    args = ap.parse_args()
    names = [c.strip() for c in args.checkers.split(",") if c.strip()]
    unknown = set(names) - set(CHECKERS)
    if unknown:
        ap.error(f"unknown checkers: {', '.join(sorted(unknown))}")

    examples = fetch_examples(args.n, args.seed)
    out = Path(args.out)
    done = {}
    if out.exists():
        for line in out.read_text().splitlines():
            r = json.loads(line)
            done[(r["checker"], r["id"])] = r

    with out.open("a") as fh:
        def run(name):
            def one(ex):
                t = time.time()
                try:
                    p, err = CHECKERS[name](ex), None
                except Exception as exc:  # noqa: BLE001 - a failed check is a row, not a crash
                    p, err = None, repr(exc)[:200]
                dt = time.time() - t
                if name == "llm":
                    time.sleep(max(0.0, 4.5 - dt))  # stay under a 15 rpm quota
                row = dict(checker=name, id=ex["id"], label=ex["label"], p=p, sec=dt, err=err)
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                done[(name, ex["id"])] = row
                print(f"{name} {ex['id']} label={ex['label']} p={p} {dt:.1f}s {err or ''}", flush=True)

            todo = [ex for ex in examples if (name, ex["id"]) not in done]
            with ThreadPoolExecutor(1 if name == "llm" else 3) as pool:
                list(pool.map(one, todo))

        with ThreadPoolExecutor(len(names)) as outer:
            list(outer.map(run, names))

    ids = {ex["id"] for ex in examples}
    print("\n| checker | scored | errors | AUROC | best balanced acc | cutoff | good answers rejected | median s |")
    print("|---|---|---|---|---|---|---|---|")
    for name in names:
        rows = [r for (c, i), r in done.items() if c == name and i in ids]
        ok = [r for r in rows if r["p"] is not None]
        if not ok or len({r["label"] for r in ok}) < 2:
            print(f"| {name} | {len(ok)} | {len(rows) - len(ok)} | - | - | - | - | - |")
            continue
        ys, ps = [r["label"] for r in ok], [r["p"] for r in ok]
        bacc, cut, fp = best_cutoff(ys, ps)
        print(f"| {name} | {len(ok)} | {len(rows) - len(ok)} | {auroc(ys, ps):.3f} | {bacc:.3f} | "
              f"{cut:.2f} | {fp}/{len(ys) - sum(ys)} | {statistics.median(r['sec'] for r in ok):.1f} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
