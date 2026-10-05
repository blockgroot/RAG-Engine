"""Attach proxy-measured tokens to each answered question (plan §6.1).

A question's calls are the proxy rows that STARTED inside its window
[q_start, q_end] -- the answer path. Rows that started after q_end but before
the next question are ``background`` (calls the system made after replying:
tone, chat naming, ...). The runner leaves a 5 s gap so they land there.

The step label is a short fingerprint of the call's system prompt (or first
user message when there is none); ``STAGES`` names the ones we recognise.

Also writes the grader's answers files, one per system and run.

Run: .venv/bin/python -m evaluation.erb.join_tokens
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

BENCH = Path("evaluation/reports/bench1")
RUNS = BENCH / "runs"

# Prefix of system (or first user) message -> step name. Unmatched calls keep
# their raw prefix so nothing is silently merged.
STAGES = [
    ("You are an assistant for this workspace", "handbook:generate"),
    ("Classify the USER MESSAGE", "handbook:tone"),
    ("You are a helpful and precise assistant that generates answers", "basic:generate"),
]


def stage_of(row: dict) -> str:
    head = (row.get("system_head") or row.get("first_user_head") or "").strip()
    for prefix, name in STAGES:
        if head.startswith(prefix):
            return name
    return "raw:" + " ".join(head.split())[:48]


def main() -> None:
    proxy = sorted((json.loads(line) for line in (BENCH / "proxy.jsonl").open()), key=lambda r: r["ts_start"])
    split = {q["question_id"]: q["split"] for q in json.loads((BENCH / "manifest.json").read_text())["questions"]}
    for records_file in sorted(RUNS.glob("*.records.jsonl")):
        system = records_file.name.removesuffix(".records.jsonl")
        records = sorted((json.loads(line) for line in records_file.open()), key=lambda r: r["q_start"])
        joined, answers = [], defaultdict(list)
        for i, rec in enumerate(records):
            # Background = the runner's 5 s gap only, never the next question
            # (which may belong to another system file).
            next_start = rec["q_end"] + 5.5
            if i + 1 < len(records):
                next_start = min(next_start, records[i + 1]["q_start"])
            calls = [p for p in proxy if rec["q_start"] <= p["ts_start"] < next_start]
            main_calls = [c for c in calls if c["ts_start"] <= rec["q_end"]]
            bg_calls = [c for c in calls if c["ts_start"] > rec["q_end"]]

            def total(cs, key):
                return sum(c.get(key) or 0 for c in cs)

            steps: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input": 0, "output": 0})
            for c in calls:
                s = steps[stage_of(c) + (" [background]" if c in bg_calls else "")]
                s["calls"] += 1
                s["input"] += c.get("input_tokens") or 0
                s["output"] += c.get("output_tokens") or 0
            rec["tokens"] = {
                "answer_path_input": total(main_calls, "input_tokens"),
                "answer_path_output": total(main_calls, "output_tokens"),
                "answer_path_reasoning": total(main_calls, "reasoning_tokens"),
                "background_input": total(bg_calls, "input_tokens"),
                "background_output": total(bg_calls, "output_tokens"),
                "calls": len(main_calls),
                "background_calls": len(bg_calls),
                "unmetered_calls": sum(1 for c in calls if c.get("input_tokens") is None),
                "failed_calls": sum(1 for c in calls if c.get("status") != 200),
                "max_call_input": max((c.get("input_tokens") or 0 for c in calls), default=0),
                "rate_limit_wait_s": round(sum(c.get("waited_s") or 0 for c in calls), 1),
                "steps": dict(steps),
            }
            rec["tokens"]["answer_path_total"] = rec["tokens"]["answer_path_input"] + rec["tokens"]["answer_path_output"]
            rec["tokens"]["all_total"] = rec["tokens"]["answer_path_total"] + total(bg_calls, "input_tokens") + total(bg_calls, "output_tokens")
            joined.append(rec)
            answers[(split[rec["question_id"]], rec["run"])].append(
                {"question_id": rec["question_id"], "answer": rec["answer"], "document_ids": rec["document_ids"]})
        (RUNS / f"{system}.joined.jsonl").write_text("".join(json.dumps(r) + "\n" for r in joined))
        for (sp, run), rows in answers.items():
            (RUNS / f"{system}.{sp}.run{run}.answers.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"{system}: {len(joined)} records, sets {sorted(answers)}")


if __name__ == "__main__":
    main()
