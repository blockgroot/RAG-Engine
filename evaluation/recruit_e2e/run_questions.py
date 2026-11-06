#!/usr/bin/env python3
"""Ask every question in questions.json through the real /chat/stream, as one
user, and check each reply against the answer worked out from the rows.

A reply passes when it holds every ``must`` value (commas in numbers ignored);
a chart's values count too. A ``refuse`` question passes when the reply states
no figure. The verdict is a first pass: read the FAIL and CHECK lines.

Usage:
    .venv/bin/python evaluation/recruit_e2e/run_questions.py <email> [numbers...]
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
logging.disable(logging.WARNING)

from fastapi.testclient import TestClient  # noqa: E402

from app.api.main import create_app  # noqa: E402
from app.auth import create_session_token  # noqa: E402
from app.auth.users import get_user_by_email  # noqa: E402
from app.db import close_pool  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "sales_e2e"))
from run_questions import ask  # noqa: E402

import time  # noqa: E402

from app.llm import openai_provider  # noqa: E402

#: Every model call this process makes, for the cost columns: calls, tokens in, out.
SPEND = {"calls": 0, "in": 0, "out": 0}
_create = openai_provider.OpenAICompatProvider._create


def _counted(self, kwargs):
    response = _create(self, kwargs)
    usage = getattr(response, "usage", None)
    SPEND["calls"] += 1
    SPEND["in"] += getattr(usage, "prompt_tokens", 0) or 0
    SPEND["out"] += getattr(usage, "completion_tokens", 0) or 0
    return response


openai_provider.OpenAICompatProvider._create = _counted

from app.rag import pipeline as _pipeline  # noqa: E402

#: The text every answer of this question was written from: what the number
#: check compares the reply against.
SHOWN: list[str] = []
_generate_text = _pipeline.RagPipeline._generate_text


def _seen(self, stage, prompt, **kwargs):
    if stage == _pipeline.STAGE_GENERATE:
        SHOWN.append(prompt)
    return _generate_text(self, stage, prompt, **kwargs)


_pipeline.RagPipeline._generate_text = _seen
_NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def unsupported(answer: str) -> list[str]:
    """Numbers in the answer that appear nowhere in what it was written from."""
    if not SHOWN:
        return []
    shown = {n.replace(",", "") for p in SHOWN for n in _NUMBER.findall(p)}
    said = _NUMBER.findall(re.sub(r"\[\d+\]", "", answer or ""))
    return [n for n in said if n.replace(",", "") not in shown]


def _flat(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d)", "", (text or "").lower())


def verdict(q: dict, reply: dict) -> str:
    text = str(reply.get("answer") or "")
    chart = reply.get("chart") or {}
    if isinstance(chart, dict):
        text += " " + json.dumps(chart.get("points") or [], default=str) + " " + str(chart.get("title"))
    flat = _flat(text)
    if q.get("refuse"):
        return "PASS" if not re.search(r"\b\d{2,}\b", _flat(str(reply.get("answer")))) else "CHECK"
    missing = [m for m in q.get("must", []) if _flat(str(m)) not in flat]
    if not q.get("must"):
        return "CHECK"
    return "PASS" if not missing else f"FAIL missing {missing}"


def main() -> None:
    user = get_user_by_email(sys.argv[1])
    only = {int(a) for a in sys.argv[2:]}
    client, cookies = TestClient(create_app()), {"session": create_session_token(user)}
    out_path = HERE / os.environ.get("RECRUIT_E2E_RESULTS", "results.json")
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    tally: dict[str, int] = {}
    try:
        for q in json.loads((HERE / "questions.json").read_text()):
            if only and q["n"] not in only:
                continue
            turns = q["question"].split(" -> ")
            conv = client.post("/chat/conversations", json={}, cookies=cookies).json()["conversation_id"]
            before, started = dict(SPEND), time.perf_counter()
            SHOWN.clear()
            replies = [ask(client, cookies, t, q["mode"] == "chart", conv) for t in turns]
            cost = {k: SPEND[k] - before[k] for k in SPEND}
            cost["seconds"] = round(time.perf_counter() - started, 1)
            cost["unsupported"] = unsupported(str(replies[-1].get("answer") or ""))
            v = verdict(q, replies[-1])
            tally[v.split()[0]] = tally.get(v.split()[0], 0) + 1
            results[str(q["n"])] = {**q, "verdict": v, "cost": cost, "replies": replies}
            print(f"#{q['n']} {v} | {q['question'][:70]!r} | {cost}\n    expect: {q['expect'][:150]}\n"
                  f"    got:    {str(replies[-1]['answer'])[:300]!r}", flush=True)
            out_path.write_text(json.dumps(results, indent=1, default=str, ensure_ascii=False))
    finally:
        close_pool()
    costs = [r.get("cost") or {} for k, r in results.items() if not only or int(k) in only]
    total = {k: sum(c.get(k, 0) for c in costs) for k in ("calls", "in", "out", "seconds")}
    total["answers_with_unsupported_numbers"] = sum(bool(c.get("unsupported")) for c in costs)
    print(tally, "| per question:", {k: round(v / max(len(costs), 1), 1) for k, v in total.items()})
    # A release check: RECRUIT_E2E_MIN_PASS=45 fails the run below 45 passes.
    floor = int(os.environ.get("RECRUIT_E2E_MIN_PASS", "0") or 0)
    if tally.get("PASS", 0) < floor:
        sys.exit(f"{tally.get('PASS', 0)} passed, below the floor of {floor}")


if __name__ == "__main__":
    main()
