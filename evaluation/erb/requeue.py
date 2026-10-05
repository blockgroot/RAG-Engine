"""Send back answers the free-tier infrastructure spoiled, so they are asked again.

Two cases, both caused by our free providers rather than by the system under test:
- Handbook / basic answers that took over 150 s. Neither pipeline takes that long
  on its own; it is a NVIDIA call that hung (before the proxy retried hangs) or
  the run being paused mid-question.
- Onyx answers that searched but got NO documents. Jina's free tier rate-limited
  every search, and Onyx's own error handling then crashed the search tool
  ("can only concatenate str (not "list") to str"), so Onyx answered blind.

The rows are moved, not deleted, to runs/<system>.requeued.jsonl (rule 6: keep
every raw answer), and the runner re-asks them on its next pass.

Run: .venv/bin/python -m evaluation.erb.requeue
"""

from __future__ import annotations

import json
from pathlib import Path

RUNS = Path("evaluation/reports/bench1/runs")
SLOW_SECONDS = 150


def spoiled(rec: dict) -> str | None:
    if rec["system"] in ("handbook", "basic") and rec["seconds"] > SLOW_SECONDS:
        return f"took {rec['seconds']:.0f}s (provider hang or pause)"
    if rec["system"] == "onyx" and rec["raw"].get("tool_calls") and not rec["document_ids"]:
        return "searched but got no documents (Jina rate limit crashed Onyx's search)"
    return None


def main() -> None:
    for records in sorted(RUNS.glob("*.records.jsonl")):
        rows = [json.loads(line) for line in records.open()]
        keep, moved = [], []
        for r in rows:
            reason = spoiled(r)
            (moved if reason else keep).append({**r, "requeue_reason": reason} if reason else r)
        if moved:
            with records.with_name(records.name.replace(".records.", ".requeued.")).open("a") as f:
                f.writelines(json.dumps(r) + "\n" for r in moved)
            records.write_text("".join(json.dumps(r) + "\n" for r in keep))
        for r in moved:
            print(f"requeued {r['system']} {r['question_id']} run{r['run']}: {r['requeue_reason']}")


if __name__ == "__main__":
    main()
