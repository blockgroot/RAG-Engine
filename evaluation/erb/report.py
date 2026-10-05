"""Build REPORT.md for Benchmark 1 from the raw files only (plan §10).

Inputs (all under evaluation/reports/bench1/):
  runs/<system>.joined.jsonl                 answers + proxy tokens per question/run
  grades/<system>.run<k>.<judge>.json        EnterpriseRAG-Bench grader output
  manifest.json, pilot_questions.jsonl, proxy.jsonl

Correct = BOTH judges say correct (strict). Each judge's own rate and their
agreement are printed beside it, so the strict rule is visible, not hidden.

Buckets (benchmarks.md "How to grade each answer"):
  refused + judged correct   -> honest_idk     refused + judged wrong -> wrong_idk
  answered + correct, 100%   -> correct        answered + correct, <100% -> partial
  answered + judged wrong    -> made_up
"""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from pathlib import Path

BENCH = Path("evaluation/reports/bench1")
JUDGES = ("qwen", "gemini")
SYSTEMS = ("handbook", "onyx", "basic")
LABELS = {"handbook": "Handbook (core mode)", "onyx": "Onyx v4.8.4", "basic": "Basic search, top 10 chunks"}
BREADTH = {"completeness", "high_level"}
SWEEP_JUDGE = "gemini"  # dev-only experiment, one judge

# Frozen after reading the dev answers. Handbook refusals come from the
# pipeline's own flag; the other systems are detected from their text.
REFUSAL = re.compile(
    r"\b(i (do not|don't) (know|have)|no (relevant )?information|not (mentioned|found|available|provided|included)"
    r"|(does|do) not (contain|include|mention|provide|specify)|unable to (find|determine|answer)"
    r"|(couldn't|could not|cannot|can't) (find|determine|answer|locate))\b",
    re.I,
)


def load_jsonl(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.open()] if p.exists() else []


def grades_for(system: str, run: int, split: str = "test") -> dict[str, dict[str, dict]]:
    out = {}
    for judge in JUDGES:
        p = BENCH / "grades" / f"{system}.{split}.run{run}.{judge}.json"
        if p.exists():
            out[judge] = {q["question_id"]: q for q in json.loads(p.read_text())["questions"]}
    return out


def refused(rec: dict) -> bool:
    if rec["system"].startswith("handbook"):
        return bool(rec.get("refused"))
    text = (rec.get("answer") or "").strip()
    return not text or (len(text) < 400 and bool(REFUSAL.search(text)))


def bucket(rec: dict, verdicts: list[dict]) -> str | None:
    if not verdicts:
        return None
    correct = all(v["answer_correct"] for v in verdicts)
    if refused(rec):
        return "honest_idk" if correct else "wrong_idk"
    if not correct:
        return "made_up"
    return "correct" if min(v["completeness_pct"] for v in verdicts) >= 100 else "partial"


def spread(values: list[float], fmt: str = "{:.0f}") -> str:
    values = [v for v in values if v is not None]
    if not values:
        return "n/a"
    mean = statistics.mean(values)
    if len(values) == 1:
        return fmt.format(mean)
    return f"{fmt.format(mean)} ({fmt.format(min(values))}–{fmt.format(max(values))})"


def run_metrics(recs: list[dict], grades: dict, gold: dict[str, set]) -> dict:
    n = len(recs)
    buckets = defaultdict(int)
    per_judge_correct = {j: 0 for j in grades}
    agree = 0
    tok_all = sum(r["tokens"]["all_total"] for r in recs)
    tok_path = sum(r["tokens"]["answer_path_total"] for r in recs)
    prec, recall, docs_sent = [], [], []
    for r in recs:
        verdicts = [grades[j][r["question_id"]] for j in grades if r["question_id"] in grades[j]]
        b = bucket(r, verdicts)
        if b:
            buckets[b] += 1
        for j in grades:
            if r["question_id"] in grades[j] and grades[j][r["question_id"]]["answer_correct"]:
                per_judge_correct[j] += 1
        if len(verdicts) == 2 and verdicts[0]["answer_correct"] == verdicts[1]["answer_correct"]:
            agree += 1
        sent = set(r["document_ids"])
        docs_sent.append(len(sent))
        g = gold.get(r["question_id"]) or set()
        if g:
            recall.append(100 * len(sent & g) / len(g))
            if sent:
                prec.append(len(sent & g) / len(sent))
    correct = buckets["correct"]
    graded = sum(buckets.values())
    return {
        "n": n,
        "graded": graded,
        "tokens_per_correct": tok_all / correct if correct else None,
        "tokens_per_correct_path": tok_path / correct if correct else None,
        "tokens_per_q_all": tok_all / n if n else None,
        "tokens_per_q_path": tok_path / n if n else None,
        "input_per_q": sum(r["tokens"]["answer_path_input"] for r in recs) / n if n else None,
        "output_per_q": sum(r["tokens"]["answer_path_output"] for r in recs) / n if n else None,
        "calls_per_q": sum(r["tokens"]["calls"] + r["tokens"]["background_calls"] for r in recs) / n if n else None,
        "max_call_input": max((r["tokens"]["max_call_input"] for r in recs), default=0),
        "docs_sent": statistics.mean(docs_sent) if docs_sent else None,
        "context_precision": statistics.mean(prec) if prec else None,
        "recall": statistics.mean(recall) if recall else None,
        "correct_pct": 100 * correct / graded if graded else None,
        "made_up_pct": 100 * buckets["made_up"] / graded if graded else None,
        "wrong_idk_pct": 100 * buckets["wrong_idk"] / graded if graded else None,
        "honest_idk_pct": 100 * buckets["honest_idk"] / graded if graded else None,
        "partial_pct": 100 * buckets["partial"] / graded if graded else None,
        "judge_correct_pct": {j: 100 * c / n for j, c in per_judge_correct.items()},
        "agreement_pct": 100 * agree / n if len(grades) == 2 and n else None,
        "failed_calls": sum(r["tokens"]["failed_calls"] for r in recs),
        "errors": sum(1 for r in recs if r["raw"].get("exception") or r["raw"].get("error") or r["raw"].get("http")),
    }


def main() -> None:
    manifest = json.loads((BENCH / "manifest.json").read_text())
    split = {q["question_id"]: q["split"] for q in manifest["questions"]}
    questions = {q["question_id"]: q for q in load_jsonl(BENCH / "pilot_questions.jsonl")}
    gold = {qid: set(q["expected_doc_ids"]) for qid, q in questions.items()}
    meta = json.loads((BENCH / "settings.json").read_text())

    table, steps_by_system, breadth = {}, {}, {}
    for system in SYSTEMS:
        recs = [r for r in load_jsonl(BENCH / "runs" / f"{system}.joined.jsonl") if split[r["question_id"]] == "test"]
        runs = sorted({r["run"] for r in recs})
        per_run = [run_metrics([r for r in recs if r["run"] == k], grades_for(system, k), gold) for k in runs]
        table[system] = per_run
        steps = defaultdict(lambda: [0, 0, 0])
        for r in recs:
            for name, s in r["tokens"]["steps"].items():
                steps[name][0] += s["calls"]
                steps[name][1] += s["input"]
                steps[name][2] += s["output"]
        steps_by_system[system] = (steps, len(recs))
        b = [r for r in recs if r["question_type"] in BREADTH]
        breadth[system] = (statistics.mean(r["tokens"]["all_total"] for r in b) if b else None,
                           statistics.mean(r["tokens"]["all_total"] for r in recs if r["question_type"] not in BREADTH) if recs else None)

    def col(system, key, fmt="{:.0f}"):
        return spread([m[key] for m in table[system]], fmt)

    L = []
    L.append("# Benchmark 1: Token use and context management, Handbook vs Onyx\n")
    L.append(f"> **Pilot, not the full benchmark.** {len(manifest['questions'])} EnterpriseRAG-Bench questions "
             f"(25 dev / 25 test, all 10 types), {len(manifest['gold_doc_ids'])} gold + "
             f"{len(manifest['noise_doc_ids'])} noise documents (the real corpus has ~507,000). Less noise makes search "
             "easier, so accuracy here is higher than the full benchmark would show. Token use is measured exactly. "
             "Free-tier judges.\n")
    L.append(f"Generated {meta['generated']} · Handbook commit `{meta['handbook_commit']}` · Onyx `{meta['onyx_version']}` · "
             f"answer model `{meta['answer_model']}` on {meta['answer_host']}, reasoning effort `{meta['reasoning_effort']}` · "
             f"judges {', '.join(meta['judges'])}\n")

    L.append("## Main result (test split, mean over 3 runs, min–max in brackets)\n")
    L.append("| | " + " | ".join(LABELS[s] for s in SYSTEMS) + " |")
    L.append("| --- | " + " | ".join("---" for _ in SYSTEMS) + " |")
    rows = [
        ("**LLM tokens per correct answer** (all calls)", "tokens_per_correct", "{:,.0f}"),
        ("LLM tokens per correct answer (answer path only)", "tokens_per_correct_path", "{:,.0f}"),
        ("LLM tokens per question (all calls)", "tokens_per_q_all", "{:,.0f}"),
        ("— of which input", "input_per_q", "{:,.0f}"),
        ("— of which output (incl. reasoning)", "output_per_q", "{:,.0f}"),
        ("LLM calls per question", "calls_per_q", "{:.2f}"),
        ("Largest single request (input tokens)", "max_call_input", "{:,.0f}"),
        ("Documents sent to the model", "docs_sent", "{:.1f}"),
        ("Context precision (gold docs sent ÷ docs sent)", "context_precision", "{:.2f}"),
        ("Document recall %", "recall", "{:.0f}"),
        ("**Correct %** (both judges)", "correct_pct", "{:.0f}"),
        ("Partial %", "partial_pct", "{:.0f}"),
        ("Honest \"I don't know\" %", "honest_idk_pct", "{:.0f}"),
        ("**Made up %**", "made_up_pct", "{:.0f}"),
        ("**Wrong \"I don't know\" %**", "wrong_idk_pct", "{:.0f}"),
        ("Judges agree on correct/incorrect %", "agreement_pct", "{:.0f}"),
        ("Failed LLM calls / errored questions", None, None),
    ]
    for label, key, fmt in rows:
        if key is None:
            L.append(f"| {label} | " + " | ".join(
                f"{sum(m['failed_calls'] for m in table[s])} / {sum(m['errors'] for m in table[s])}" for s in SYSTEMS) + " |")
        else:
            L.append(f"| {label} | " + " | ".join(col(s, key, fmt) for s in SYSTEMS) + " |")
    for j in JUDGES:
        L.append(f"| Correct % by {j} alone | " + " | ".join(
            spread([m["judge_correct_pct"].get(j) for m in table[s]]) for s in SYSTEMS) + " |")
    L.append("")

    L.append("## Where the tokens go (test split, per question)\n")
    for system in SYSTEMS:
        steps, n = steps_by_system[system]
        if not n:
            continue
        L.append(f"**{LABELS[system]}**\n")
        L.append("| Step | Calls / question | Input / question | Output / question |")
        L.append("| --- | --- | --- | --- |")
        for name, (c, i, o) in sorted(steps.items(), key=lambda kv: -(kv[1][1] + kv[1][2])):
            L.append(f"| {name} | {c / n:.2f} | {i / n:,.0f} | {o / n:,.0f} |")
        L.append("")

    L.append("## \"Find everything\" questions, tracked apart (test split)\n")
    L.append("| System | Tokens/question, completeness + high-level types | Tokens/question, all other types |")
    L.append("| --- | --- | --- |")
    for s in SYSTEMS:
        a, b = breadth[s]
        L.append(f"| {LABELS[s]} | {a:,.0f} | {b:,.0f} |" if a and b else f"| {LABELS[s]} | n/a | n/a |")
    L.append("")

    sweep = [("handbook-topk3", 3), ("handbook", 5), ("handbook-topk10", 10), ("handbook-topk20", 20)]
    sweep_rows = []
    for name, k in sweep:
        recs = [r for r in load_jsonl(BENCH / "runs" / f"{name}.joined.jsonl") if split[r["question_id"]] == "dev" and r["run"] == 1]
        if recs:
            g = {j: v for j, v in grades_for(name, 1, "dev").items() if j == SWEEP_JUDGE}
            sweep_rows.append((k, run_metrics(recs, g, gold)))
    if sweep_rows:
        L.append(f"## Context size vs accuracy (Handbook, dev split, 1 run, {SWEEP_JUDGE} judge)\n")
        L.append(f"| top_k (chunks) | Tokens / question | Input / question | Correct % ({SWEEP_JUDGE}) | Recall % | Context precision |")
        L.append("| --- | --- | --- | --- | --- | --- |")
        for k, m in sweep_rows:
            L.append(f"| {k} | {m['tokens_per_q_all']:,.0f} | {m['input_per_q']:,.0f} | "
                     f"{m['judge_correct_pct'].get(SWEEP_JUDGE, 0):.0f} | {m['recall']:.0f} | {m['context_precision']:.2f} |")
        L.append("")

    L.append((BENCH / "notes.md").read_text() if (BENCH / "notes.md").exists() else "")
    (BENCH / "REPORT.md").write_text("\n".join(L))
    print("\n".join(L))


if __name__ == "__main__":
    main()
