"""Build REPORT.md for Benchmark 1 from the raw files only.

Inputs (all under evaluation/reports/bench1/):
  runs/<system>.joined.jsonl   answers + proxy tokens + timings per question/run
  reviews/<system>.jsonl       one review verdict per question/run (review.py)
  manifest.json, pilot_questions.jsonl

Buckets (benchmarks.md "How to grade each answer"), from one verdict:
  refused + correct            -> honest_idk     refused + not correct -> wrong_idk
  answered + unsupported claim -> made_up        (a claim the context does not back)
  answered + grounded + wrong  -> wrong          (tracked apart: wrong but not invented)
  answered + grounded + correct, all facts -> correct, else partial

Main table = test split, run 1 (150 questions). Run-to-run spread = the 50
repeat questions over 3 runs. Dev answers are kept but not reported (rule 3).

Also writes handcheck.csv: a seeded 10% of reviewed test answers per system,
for a person to check against the reviewer (rule 5).
"""

from __future__ import annotations

import csv
import json
import os
import random
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env.bench", override=True)

BENCH = Path("evaluation/reports/bench1")
# v2 reviews replace v1 once they exist (v1 failed its rule-5 check; see review.py).
REVIEWS = BENCH / ("reviews_v2" if (BENCH / "reviews_v2").exists() else "reviews")
SYSTEMS = ("handbook", "onyx", "basic")
LABELS = {"handbook": "Handbook (core mode)", "onyx": "Onyx v4.8.4", "basic": "Basic search, top 10 (reference)"}
BREADTH = {"completeness", "high_level"}
# Published price of the answer model (OpenRouter, nemotron-3-super-120b-a12b), $ per 1M tokens.
PRICE_IN, PRICE_OUT = 0.09, 0.45
WINS = ("correct", "honest_idk")
RUNAWAY_TOKENS = 100_000


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.open()] if p.exists() else []


def bucket(v: dict | None, n_facts: int) -> str | None:
    if not v:
        return None
    if v.get("refused"):
        return "honest_idk" if v.get("correct") else "wrong_idk"
    if v.get("unsupported_claims"):
        return "made_up"
    if not v.get("correct"):
        return "wrong"
    if v.get("wrong_source_claims"):  # v2: right main point, a detail taken from the wrong document
        return "partial"
    facts = v.get("facts") or []
    return "correct" if len(facts) >= n_facts and all(facts) else "partial"


def pct(n: int, d: int) -> float | None:
    return 100 * n / d if d else None


def pctl(values: list[float], p: float) -> float | None:
    values = sorted(values)
    return values[min(len(values) - 1, int(p * len(values)))] if values else None


def metrics(recs: list[dict]) -> dict:
    n = len(recs)
    b = Counter(r["bucket"] for r in recs if r["bucket"])
    graded = sum(b.values())
    wins = sum(b[k] for k in WINS)
    tok = sum(r["tokens"]["all_total"] for r in recs)
    inp = sum(r["tokens"]["answer_path_input"] + r["tokens"]["background_input"] for r in recs)
    out = sum(r["tokens"]["answer_path_output"] + r["tokens"]["background_output"] for r in recs)
    cost = (inp * PRICE_IN + out * PRICE_OUT) / 1e6
    recall, prec, recall_used = [], [], []
    for r in recs:
        sent, gold = set(r["document_ids"]), r["gold"]
        # Same funnel stage for every system: the documents the answer was written
        # from. Handbook/basic: what was sent to the model. Onyx: what it cited
        # (its document_ids are every search result, most of which it discards).
        used = set(d for d in r["raw"].get("cited") or [] if d) if r["system"].startswith("onyx") else sent
        if gold:
            recall.append(100 * len(sent & gold) / len(gold))
            recall_used.append(100 * len(used & gold) / len(gold))
            if sent:
                prec.append(len(sent & gold) / len(sent))
    facts = [100 * sum(r["verdict"]["facts"]) / len(r["verdict"]["facts"])
             for r in recs if r["verdict"] and r["verdict"].get("facts")]
    return {
        "n": n, "graded": graded,
        "tokens_per_win": tok / wins if wins else None,
        "tokens_per_q": tok / n if n else None,
        "tokens_median": statistics.median(r["tokens"]["all_total"] for r in recs) if recs else None,
        "tokens_p90": pctl([r["tokens"]["all_total"] for r in recs], 0.9),
        # A call that hit the model's output ceiling without answering (seen on
        # Onyx's uncapped section-selection call: 131,072 reasoning tokens).
        "runaway_answers": sum(1 for r in recs if r["tokens"].get("max_call_output", 0) >= RUNAWAY_TOKENS),
        "input_per_q": inp / n if n else None,
        "output_per_q": out / n if n else None,
        "cost_per_q": cost / n if n else None,
        "cost_per_win": cost / wins if wins else None,
        "calls_per_q": sum(r["tokens"]["calls"] + r["tokens"]["background_calls"] for r in recs) / n if n else None,
        "max_call_input": max((r["tokens"]["max_call_input"] for r in recs), default=0),
        "docs_sent": statistics.mean(len(set(r["document_ids"])) for r in recs) if recs else None,
        "context_precision": statistics.mean(prec) if prec else None,
        "recall": statistics.mean(recall) if recall else None,
        "recall_used": statistics.mean(recall_used) if recall_used else None,
        "win_pct": pct(wins, graded),
        **{f"{k}_pct": pct(b[k], graded) for k in ("correct", "partial", "honest_idk", "wrong_idk", "made_up", "wrong")},
        "correct_any_pct": pct(sum(1 for r in recs if r["verdict"] and r["verdict"].get("correct")), graded),
        "grounded_pct": pct(sum(1 for r in recs if r["verdict"] and not r["verdict"].get("unsupported_claims")), graded),
        "facts_pct": statistics.mean(facts) if facts else None,
        "ttfw_median": statistics.median(r["ttfw_s"] for r in recs) if recs else None,
        "ttfw_p90": pctl([r["ttfw_s"] for r in recs], 0.9),
        "total_median": statistics.median(r["total_s"] for r in recs) if recs else None,
        "failed_calls": sum(r["tokens"]["failed_calls"] for r in recs),
        "errors": sum(1 for r in recs if any(r["raw"].get(k) for k in ("exception", "error", "http", "error_msg"))),
        "unreviewed": n - graded,
        # Searched but got no documents: Onyx's search tool crashed (Jina free-tier
        # 429 hitting an Onyx error-handling bug) on every search it tried.
        "blind_answers": sum(1 for r in recs if r["raw"].get("tool_calls") and not r["document_ids"]),
        "win_pct_sighted": pct(sum(1 for r in recs if r["bucket"] in WINS and not (r["raw"].get("tool_calls") and not r["document_ids"])),
                               sum(1 for r in recs if r["bucket"] and not (r["raw"].get("tool_calls") and not r["document_ids"]))),
        "searched_pct": None if recs and recs[0]["system"].startswith(("handbook", "basic"))
        else pct(sum(1 for r in recs if r["raw"].get("tool_calls")), n),
    }


def fmt(v, f="{:.0f}") -> str:
    return "n/a" if v is None else f.format(v)


def main() -> None:
    manifest = json.loads((BENCH / "manifest.json").read_text())
    questions = {q["question_id"]: q for q in load_jsonl(BENCH / "pilot_questions.jsonl")}
    repeat_ids = {q["question_id"] for q in manifest["questions"] if q["repeat"]}
    by_system: dict[str, list[dict]] = {}
    for system in SYSTEMS:
        reviews = {(r["question_id"], r["run"]): r for r in load_jsonl(REVIEWS / f"{system}.jsonl")}
        recs = []
        for r in load_jsonl(BENCH / "runs" / f"{system}.joined.jsonl"):
            if r["split"] != "test":
                continue
            q = questions[r["question_id"]]
            r["verdict"] = (reviews.get((r["question_id"], r["run"])) or {}).get("verdict")
            r["bucket"] = bucket(r["verdict"], len(q["answer_facts"]))
            r["gold"] = set(q["expected_doc_ids"])
            recs.append(r)
        by_system[system] = recs

    run1 = {s: metrics([r for r in by_system[s] if r["run"] == 1]) for s in SYSTEMS}
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    reviewer = next((r.get("reviewer") for s in SYSTEMS for r in load_jsonl(REVIEWS / f"{s}.jsonl")), "n/a")
    n_test = sum(1 for q in manifest["questions"] if q["split"] == "test")
    head = "| | " + " | ".join(LABELS[s] for s in SYSTEMS) + " |\n| --- | " + " | ".join("---" for _ in SYSTEMS) + " |"

    L = ["# Benchmark 1: Token use and context management, Handbook vs Onyx\n"]
    L.append(f"> **Pilot, not the full benchmark.** {len(manifest['questions'])} EnterpriseRAG-Bench questions "
             f"({len(manifest['questions']) - n_test} dev / {n_test} test, all 10 types) over "
             f"{len(manifest['gold_doc_ids']) + len(manifest['noise_doc_ids'])} documents "
             f"({len(manifest['gold_doc_ids'])} gold + {len(manifest['noise_doc_ids'])} noise; the real corpus has "
             "~507,000). Less noise makes search easier, so accuracy here is higher than the full benchmark would "
             "show. Tokens are measured exactly, from the provider's own usage field, by one proxy both systems call.\n")
    L.append(f"Generated {date.today()} · Handbook commit `{commit}` · Onyx `v4.8.4` · answer model "
             f"`{os.getenv('BENCH_MODEL')}` (both systems, through the proxy) · reviewer `{reviewer}` (one call per answer)\n")

    L.append(f"## Main result (test split, {n_test} questions, run 1)\n")
    L.append(head)
    rows = [
        ("**LLM tokens per correct answer**¹", "tokens_per_win", "{:,.0f}"),
        ("LLM tokens per question (mean)", "tokens_per_q", "{:,.0f}"),
        ("LLM tokens per question (median)", "tokens_median", "{:,.0f}"),
        ("LLM tokens per question (p90)", "tokens_p90", "{:,.0f}"),
        ("Answers with a runaway call (≥100k output tokens)⁵", "runaway_answers", "{:.0f}"),
        ("— of which input", "input_per_q", "{:,.0f}"),
        ("— of which output (incl. reasoning)", "output_per_q", "{:,.0f}"),
        ("Cost per question, if paid²", "cost_per_q", "${:.5f}"),
        ("Cost per correct answer, if paid²", "cost_per_win", "${:.5f}"),
        ("LLM calls per question", "calls_per_q", "{:.2f}"),
        ("Largest single request (input tokens)", "max_call_input", "{:,.0f}"),
        ("Documents retrieved (Handbook: sent to the model)", "docs_sent", "{:.1f}"),
        ("Context precision (right docs ÷ docs retrieved)", "context_precision", "{:.2f}"),
        ("Document recall %, retrieved (anywhere in the search results)⁷", "recall", "{:.0f}"),
        ("Document recall %, used for the answer⁷", "recall_used", "{:.0f}"),
        ("**Win %** (correct + honest \"I don't know\")", "win_pct", "{:.0f}"),
        ("Correct %", "correct_pct", "{:.0f}"),
        ("Partial %", "partial_pct", "{:.0f}"),
        ("Honest \"I don't know\" %", "honest_idk_pct", "{:.0f}"),
        ("**Made up %** (claim not in the context)", "made_up_pct", "{:.0f}"),
        ("**Wrong \"I don't know\" %**", "wrong_idk_pct", "{:.0f}"),
        ("Wrong but grounded %", "wrong_pct", "{:.0f}"),
        ("Main point correct % (ignoring groundedness)", "correct_any_pct", "{:.0f}"),
        ("Grounded % (no unsupported claim)", "grounded_pct", "{:.0f}"),
        ("Required facts present %", "facts_pct", "{:.0f}"),
        ("Time to first word, median s³", "ttfw_median", "{:.1f}"),
        ("Time to first word, p90 s³", "ttfw_p90", "{:.1f}"),
        ("Total time, median s³", "total_median", "{:.1f}"),
        ("Searched the documents %⁴", "searched_pct", "{:.0f}"),
        ("Answers whose every search crashed (no documents)⁶", "blind_answers", "{:.0f}"),
        ("Win % excluding those⁶", "win_pct_sighted", "{:.0f}"),
    ]
    for label, key, f in rows:
        L.append(f"| {label} | " + " | ".join(fmt(run1[s][key], f) for s in SYSTEMS) + " |")
    L.append("| Failed LLM calls / errored questions / unreviewed | " + " | ".join(
        f"{run1[s]['failed_calls']} / {run1[s]['errors']} / {run1[s]['unreviewed']}" for s in SYSTEMS) + " |")
    L.append("")
    L.append("¹ All LLM tokens (answer path + background) ÷ wins. ² At the answer model's published price, "
             f"${PRICE_IN} / ${PRICE_OUT} per 1M input / output tokens; the run itself used a free tier. "
             "³ Free-tier rate-limit waits removed. Handbook streams only an already-decided answer, so its first "
             "word is the end of the pipeline; Onyx's is the first token of its answer call. "
             "⁵ A model call that kept reasoning until the model's 131,072-token output ceiling without answering. "
             "Onyx sends its section-selection call with no output cap; Handbook caps its answer call (8,000 here). "
             "Counted in every token figure, as it is real spend. "
             "⁶ Onyx's search tool crashes when Jina's free tier rate-limits its embedding calls (an error-formatting "
             "bug in Onyx: `can only concatenate str (not \"list\") to str`). When every search in an answer crashed, Onyx "
             "answered with no documents. These were asked a second time; the ones still blind are counted in every "
             "figure as Onyx's answer, and win % without them is shown so the free-tier effect is visible. "
             "⁷ Retrieved: Handbook and basic send everything they retrieve to the model, so both rows match for them; "
             "Onyx retrieves ~45–50 documents across all its searches, shows its selection step ~32 sections from "
             "~26 of them, keeps ~2, and cites ~1. Used for the answer = Handbook/basic: the documents sent to the model; "
             "Onyx: the documents its answer cited (a lower bound: an answer can use a section without citing it). "
             "⁴ Onyx decides per question whether to search; Handbook and the basic reference always retrieve. The basic reference is plain top-10 vector search plus EnterpriseRAG-Bench's own answer prompt in one call, on Handbook's index: not a product, it shows what each product's extra steps buy.\n")

    L.append(f"## Run-to-run variation ({len(repeat_ids)} repeat test questions, 3 runs)\n")
    L.append(head)
    rep = {}
    for s in SYSTEMS:
        recs = [r for r in by_system[s] if r["question_id"] in repeat_ids]
        rep[s] = (recs, [metrics([r for r in recs if r["run"] == k]) for k in (1, 2, 3)])

    def spread(s, key, f):
        vals = [m[key] for m in rep[s][1] if m["n"] and m[key] is not None]
        if not vals:
            return "n/a"
        return f"{f.format(statistics.mean(vals))} ({f.format(min(vals))}–{f.format(max(vals))})"

    for label, key, f in [("Win %, mean (min–max)", "win_pct", "{:.0f}"), ("Made up %", "made_up_pct", "{:.0f}"),
                          ("Wrong \"I don't know\" %", "wrong_idk_pct", "{:.0f}"),
                          ("Tokens per question", "tokens_per_q", "{:,.0f}")]:
        L.append(f"| {label} | " + " | ".join(spread(s, key, f) for s in SYSTEMS) + " |")
    same = {}
    for s in SYSTEMS:
        per_q = defaultdict(list)
        for r in rep[s][0]:
            per_q[r["question_id"]].append(r["bucket"])
        full = [b for b in per_q.values() if len(b) == 3 and None not in b]
        same[s] = pct(sum(1 for b in full if len(set(b)) == 1), len(full))
    L.append("| Same bucket in all 3 runs % | " + " | ".join(fmt(same[s]) for s in SYSTEMS) + " |")
    L.append("")

    L.append("## By question type (test split, run 1)\n")
    L.append("| Type | " + " | ".join(f"Win % {LABELS[s]}" for s in SYSTEMS) + " | " +
             " | ".join(f"Tokens/q {LABELS[s]}" for s in SYSTEMS) + " |")
    L.append("| --- |" + " --- |" * (2 * len(SYSTEMS)))
    for t in sorted({r["question_type"] for s in SYSTEMS for r in by_system[s]}):
        ms = [metrics([r for r in by_system[s] if r["run"] == 1 and r["question_type"] == t]) for s in SYSTEMS]
        L.append(f"| {t}{' (breadth)' if t in BREADTH else ''} | " + " | ".join(fmt(m["win_pct"]) for m in ms) +
                 " | " + " | ".join(fmt(m["tokens_per_q"], "{:,.0f}") for m in ms) + " |")
    L.append("")

    L.append("## Where the tokens go (test split, run 1, per question)\n")
    for s in SYSTEMS:
        recs = [r for r in by_system[s] if r["run"] == 1]
        if not recs:
            continue
        steps = defaultdict(lambda: [0, 0, 0])
        for r in recs:
            for name, st in r["tokens"]["steps"].items():
                steps[name][0] += st["calls"]
                steps[name][1] += st["input"]
                steps[name][2] += st["output"]
        n = len(recs)
        L.append(f"**{LABELS[s]}**\n")
        L.append("| Step | Calls / question | Input / question | Output / question |")
        L.append("| --- | --- | --- | --- |")
        for name, (c, i, o) in sorted(steps.items(), key=lambda kv: -(kv[1][1] + kv[1][2])):
            L.append(f"| {name} | {c / n:.2f} | {i / n:,.0f} | {o / n:,.0f} |")
        L.append("")

    L.append("## How this was run, and where it departs from benchmarks.md\n")
    L.append("- Same answer model, same documents, same questions for both systems (rules 1, 2). Nothing was tuned "
             "on the test split (rule 3).\n"
             f"- **Rule 4, partly:** 3 runs on {len(repeat_ids)} test questions (5 per type), 1 run on the rest.\n"
             "- **Rule 5, partly:** one reviewer call per answer (correctness, facts and groundedness together), not "
             "two judges from different companies. `handcheck.csv` holds a 10% sample for a person to check.\n"
             "- Groundedness is checked against the exact input the answer model received, captured by the proxy.\n"
             "- Scores use our own review prompt, so they are not comparable with the public EnterpriseRAG-Bench "
             "leaderboard. The Handbook-vs-Onyx comparison is fair: both get the identical review.\n"
             "- Handbook ran at its product defaults except: `RAG_MAX_ANSWER_TOKENS=8000` (default 700; the answer model reasons first, and at 2000 it was cut off mid-sentence; Onyx sets no such cap); NO per-chunk AI context line at ingest (default on; Onyx's equivalent, "
             "contextual RAG, is also off by default, so neither system has it); knowledge graph, live tools, "
             "personal memory, web search and the injection guard off (they need real connectors or users, or would "
             "let either system answer from the web). The answer cache is on, as shipped; with 200 distinct questions "
             "and a 5-minute lifetime it never hits. Onyx ran as shipped, limited to internal search, deep research off.\n"
             "- Every raw answer, proxy call and review is kept in this folder (rule 6).\n")
    L.append((BENCH / "notes.md").read_text() if (BENCH / "notes.md").exists() else "")
    (BENCH / "REPORT.md").write_text("\n".join(L))
    print("\n".join(L))

    # Written once: people (and the second reviewer) add columns to it, which a
    # regenerated report must never overwrite.
    if (BENCH / "handcheck.csv").exists():
        return
    rng = random.Random(20261005)
    with (BENCH / "handcheck.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["system", "question_id", "question", "gold_answer", "answer", "reviewer_bucket",
                    "reviewer_reason", "unsupported_claims", "person_agrees (y/n)", "person_note"])
        for s in SYSTEMS:
            pool = [r for r in by_system[s] if r["run"] == 1 and r["verdict"]]
            for r in rng.sample(pool, max(1, len(pool) // 10)) if pool else []:
                q = questions[r["question_id"]]
                w.writerow([s, r["question_id"], q["question"], q["gold_answer"], r["answer"], r["bucket"],
                            r["verdict"].get("reason"), "; ".join(r["verdict"].get("unsupported_claims") or []), "", ""])


if __name__ == "__main__":
    main()
