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
            replies = [ask(client, cookies, t, q["mode"] == "chart", conv) for t in turns]
            v = verdict(q, replies[-1])
            tally[v.split()[0]] = tally.get(v.split()[0], 0) + 1
            results[str(q["n"])] = {**q, "verdict": v, "replies": replies}
            print(f"#{q['n']} {v} | {q['question'][:70]!r}\n    expect: {q['expect'][:150]}\n"
                  f"    got:    {str(replies[-1]['answer'])[:300]!r}", flush=True)
            out_path.write_text(json.dumps(results, indent=1, default=str, ensure_ascii=False))
    finally:
        close_pool()
    print(tally)


if __name__ == "__main__":
    main()
