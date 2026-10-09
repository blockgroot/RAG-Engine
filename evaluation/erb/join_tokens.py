"""Attach proxy-measured tokens to each answered question (plan §6.1).

A question's calls are the proxy rows that STARTED inside its window
[q_start, q_end] -- the answer path. Rows that started after q_end but before
the next question are ``background`` (calls the system made after replying:
tone, chat naming, ...). The runner leaves a 5 s gap so they land there.

The step label is a short fingerprint of the call's system prompt (or first
user message when there is none); ``STAGES`` names the ones we recognise.

The ANSWER CALL is the last answer-path call that is not Handbook's tone
classifier: the call whose output the person reads. ``review.py`` checks
groundedness against its request (``bodies.jsonl``).

Time to first word (benchmarks.md §5): Handbook streams only an already-decided
answer, so its first word is the end of the pipeline. Onyx streams its answer
call, so its first word is that call's first content token (or its end when the
call was not streamed). Both are reported with rate-limit waits removed -- a
free-tier queue is not product latency.

Run: .venv/bin/python -m evaluation.erb.join_tokens
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path

BENCH = Path(os.getenv("BENCH_DIR", "evaluation/reports/bench1"))
RUNS = BENCH / "runs"

# Prefix of system (or first user) message -> step name. Unmatched calls keep
# their raw prefix so nothing is silently merged.
STAGES = [
    ("You are an assistant for this workspace", "handbook:generate"),
    ("Classify the USER MESSAGE", "handbook:tone"),
    ("You analyze a user question for a company policy", "handbook:split compound question"),
    ("You help a document-search system recover", "handbook:search recovery"),
    ("You are a helpful and precise assistant that generates answers", "basic:generate"),
    ("You are an expert assistant who is truthful", "onyx:answer (tool choice, then final answer)"),
    ("You are an assistant that reformulates the last", "onyx:query rewrite"),
    ("You scope an internal search to a time filter", "onyx:time filter"),
    ("Select the most relevant document sections", "onyx:section selection"),
    ("Analyze the relevance of document sections", "onyx:section relevance"),
]


def stage_of(row: dict) -> str:
    head = (row.get("system_head") or row.get("first_user_head") or "").strip()
    for prefix, name in STAGES:
        if head.startswith(prefix):
            return name
    return "raw:" + " ".join(head.split())[:48]


def main() -> None:
    shared = sorted((json.loads(line) for line in (BENCH / "proxy.jsonl").open()), key=lambda r: r["ts_start"])
    split = {q["question_id"]: q["split"] for q in json.loads((BENCH / "manifest.json").read_text())["questions"]}
    for records_file in sorted(RUNS.glob("*.records.jsonl")):
        system = records_file.name.removesuffix(".records.jsonl")
        # A system run at the same time as another has its own proxy and log
        # (proxy.<system>.jsonl): calls are matched to questions by time, so two
        # systems in one log would take each other's calls.
        own = BENCH / f"proxy.{system}.jsonl"
        proxy = sorted((json.loads(line) for line in own.open()), key=lambda r: r["ts_start"]) if own.exists() else shared
        latest = {}  # a retried question replaces its errored row
        for line in records_file.open():
            r = json.loads(line)
            latest[(r["question_id"], r["run"])] = r
        records = sorted(latest.values(), key=lambda r: r["q_start"])
        joined = []
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
                "max_call_output": max((c.get("output_tokens") or 0 for c in calls), default=0),
                "rate_limit_wait_s": round(sum(c.get("waited_s") or 0 for c in calls), 1),
                "steps": dict(steps),
            }
            rec["tokens"]["answer_path_total"] = rec["tokens"]["answer_path_input"] + rec["tokens"]["answer_path_output"]
            rec["tokens"]["all_total"] = rec["tokens"]["answer_path_total"] + total(bg_calls, "input_tokens") + total(bg_calls, "output_tokens")
            answer_calls = [c for c in main_calls if stage_of(c) != "handbook:tone"]
            ac = answer_calls[-1] if answer_calls else None
            rec["answer_call_id"] = ac and ac.get("call_id")
            waited = rec["tokens"]["rate_limit_wait_s"]
            if rec["system"].startswith("handbook") or ac is None:
                first = rec["q_end"]
            else:
                first = ac.get("ts_first_content") or ac["ts_end"]
            waited_before_first = sum(c.get("waited_s") or 0 for c in main_calls if c["ts_start"] <= first)
            rec["ttfw_s"] = round(first - rec["q_start"] - waited_before_first, 2)
            rec["total_s"] = round(rec["seconds"] - waited, 2)
            rec["split"] = split[rec["question_id"]]
            joined.append(rec)
        (RUNS / f"{system}.joined.jsonl").write_text("".join(json.dumps(r) + "\n" for r in joined))
        print(f"{system}: {len(joined)} records")


if __name__ == "__main__":
    main()
