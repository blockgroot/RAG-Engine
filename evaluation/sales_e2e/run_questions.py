#!/usr/bin/env python3
"""Ask every Ask/Chart question in questions.md through the real /chat/stream, as one user.

Writes results.json next to this file: per question, the answer, who answered,
cited titles and any chart. Grading against the expected column is done by reading it.

Usage:
    .venv/bin/python evaluation/sales_e2e/run_questions.py <email> [question numbers...]
"""

from __future__ import annotations

import json
import logging
import os
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


def questions() -> list[dict]:
    out = []
    for line in (HERE / "questions.md").read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[0].isdigit() and cells[1] in ("Ask", "Chart", "Ask (follow-up)"):
            out.append({"n": int(cells[0]), "mode": cells[1], "question": cells[2], "expected": cells[3]})
    return out


def ask(client, cookies, question: str, chart: bool, conversation_id: str | None) -> dict:
    body = {"question": question, "conversation_id": conversation_id}
    if chart:
        body["mode"] = "chart"
    r = client.post("/chat/stream", json=body, cookies=cookies)
    events = {}
    for block in r.text.split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) >= 2 and lines[0].startswith("event: "):
            name, data = lines[0][7:], lines[1].removeprefix("data: ")
            if name != "token":
                events[name] = json.loads(data) if data[:1] in "{[\"" else data
    done = events.get("done") or {}
    chart_ev = done.get("chart")
    return {
        "status": r.status_code,
        "answer": done.get("answer") or events.get("error"),
        "grounded": done.get("grounded"),
        "source": done.get("source"),
        "agent": done.get("agent"),
        "cited": sorted({c.get("title") or c.get("document_title") or str(c)[:60]
                         for c in done.get("cited") or [] if isinstance(c, dict)}),
        "chart_hint": done.get("chart_hint"),
        "chart": {k: chart_ev.get(k) for k in ("title", "type", "explain", "points", "series")} if isinstance(chart_ev, dict) else chart_ev,
        "events": sorted(events),
    }


def main() -> None:
    user = get_user_by_email(sys.argv[1])
    only = {int(a) for a in sys.argv[2:]}
    client, cookies = TestClient(create_app()), {"session": create_session_token(user)}
    out_path = HERE / os.environ.get("SALES_E2E_RESULTS", "results.json")
    results = json.loads(out_path.read_text()) if out_path.exists() else {}
    try:
        for q in questions():
            if only and q["n"] not in only:
                continue
            chart = q["mode"] == "Chart"
            turns = q["question"].split(" → ")
            # a fresh chat per question, as the web app opens one; follow-ups share it
            conv = client.post("/chat/conversations", json={}, cookies=cookies).json()["conversation_id"]
            replies = [ask(client, cookies, t, chart, conv) for t in turns]
            results[str(q["n"])] = {**q, "replies": replies}
            print(f"#{q['n']} {q['question'][:60]!r} -> {str(replies[-1]['answer'])[:120]!r}", flush=True)
            out_path.write_text(json.dumps(results, indent=1, default=str, ensure_ascii=False))
    finally:
        close_pool()


if __name__ == "__main__":
    main()
