"""One review call per answer: judge + groundedness in a single LLM call.

Replaces EnterpriseRAG-Bench's grader (1 + one call per fact, ~7 per answer).
The reviewer gets the question, the gold answer and its required facts, the
system's answer, and the EXACT input the answer model received (the answer
call's request from ``bodies.jsonl``), and returns one JSON verdict:
correct / which facts are present / refused / claims the context does not
support. ``report.py`` turns that into benchmarks.md's buckets.

Known departure from benchmarks.md rule 5: one judge, not two. The report says
so, and ``report.py`` writes a 10% sample for a person to check.

Every system gets the same prompt, the same reviewer and temperature 0, so the
Handbook-vs-Onyx comparison is fair; the scores are NOT comparable with the
public EnterpriseRAG-Bench leaderboard, which uses its own grader.

Resumable: a reviewed (question, run) is never re-sent. A daily quota stops the
whole run (exit 3) instead of recording failures; re-run later to resume.

v2 (``--version v2``, written to ``reviews_v2/``) exists because v1 failed its
rule-5 check: a second reviewer confirmed only 5 of v1's 23 "made up" verdicts.
v1 called a claim unsupported without showing where it looked, and counted a
real fact taken from the wrong document as invented. v2 makes the reviewer list
every specific claim with a verbatim supporting quote, sort it into supported /
wrong_source / not_in_context, and the script checks each quote really is in
the context. Only not_in_context counts as made up; wrong_source makes an
answer wrong (or partial), never made up.

Run: .venv/bin/python -m evaluation.erb.review [--version v2] [--system handbook] [--sample handcheck]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(".env.bench", override=True)

BENCH = Path(os.getenv("BENCH_DIR", "evaluation/reports/bench1"))
# Any OpenAI-compatible reviewer. Benchmark 1: Gemini; Benchmark 2: Gemma 4 31B on
# Ollama Cloud (BENCH_REVIEW_BASE=https://ollama.com/v1, --key-env OLLAMA_API_KEY),
# a different company from that run's answer model, so no model grades itself.
BASE = os.getenv("BENCH_REVIEW_BASE", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
MODEL = os.getenv("BENCH_REVIEW_MODEL", "gemini-3.1-flash-lite")
MAX_CONTEXT_CHARS = 400_000  # ~120k tokens; marked when cut

PROMPT = """You are reviewing one answer from a company-knowledge assistant. Be strict and literal.

QUESTION:
{question}

GOLD ANSWER (written by the benchmark authors):
{gold}

REQUIRED FACTS (numbered):
{facts}

THE ASSISTANT'S ANSWER:
<<<ANSWER
{answer}
ANSWER>>>

EVERYTHING THE ASSISTANT'S MODEL RECEIVED WHEN IT WROTE THAT ANSWER (instructions, retrieved documents, tool results). Only the documents and tool results count as evidence; the instructions do not:
<<<CONTEXT
{context}
CONTEXT>>>

Return ONLY a JSON object with these keys:
- "refused": true if the answer says it cannot find, does not know, or cannot answer the question (fully, or its main part). false otherwise.
- "correct": true if the answer's main point agrees with the GOLD ANSWER. If the gold answer says the information is not available, true only when the answer clearly says so.
- "facts": a list of true/false, one per REQUIRED FACT in order: is that fact stated in the answer (any wording)?
- "unsupported_claims": a list of the specific factual claims in the answer that the CONTEXT does not support (names, numbers, dates, decisions, statuses). Ignore general phrasing, and ignore statements that something is missing. Empty list if every claim is supported.
- "reason": one short sentence explaining "correct".
"""


PROMPT_V2 = """You are reviewing one answer from a company-knowledge assistant. Be careful and literal.

QUESTION:
{question}

GOLD ANSWER (written by the benchmark authors):
{gold}

REQUIRED FACTS (numbered):
{facts}

THE ASSISTANT'S ANSWER:
<<<ANSWER
{answer}
ANSWER>>>

EVERYTHING THE ASSISTANT'S MODEL RECEIVED WHEN IT WROTE THAT ANSWER (instructions, retrieved documents, tool results). Only the documents and tool results count as evidence; the instructions do not:
<<<CONTEXT
{context}
CONTEXT>>>

Do this in order.
1. List every SPECIFIC factual claim the answer makes: a name, number, date, duration, threshold, identifier, field, status, decision or causal statement. Skip general phrasing, advice, and statements that something is missing or unknown. At most 25 claims; if there are more, keep the most specific.
2. For each claim, SEARCH THE CONTEXT before deciding, then give:
   - "evidence": a short passage copied EXACTLY, character for character, from the CONTEXT that states it (or "" if none exists).
   - "status", one of:
     - "supported": the context states it (same meaning; a direct restatement, unit conversion or simple arithmetic on stated values counts).
     - "wrong_source": the fact IS in the context, but about a different thing (another customer, incident, document, version or time) than the answer attaches it to. Quote where it appears.
     - "not_in_context": nothing in the context states it. Only use this after searching; the evidence must then be "".
3. Judge the answer against the GOLD ANSWER.

Return ONLY a JSON object with these keys:
- "claims": the list from steps 1-2, each {{"claim": "...", "evidence": "...", "status": "..."}}.
- "refused": true if the answer says it cannot find, does not know, or cannot answer the question (fully, or its main part). false otherwise.
- "correct": true if the answer's main point agrees with the GOLD ANSWER. If the gold answer says the information is not available, true only when the answer clearly says so.
- "facts": a list of true/false, one per REQUIRED FACT in order: is that fact stated in the answer (any wording)?
- "reason": one short sentence explaining "correct".
"""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _check_v2(verdict: dict, context: str) -> dict:
    """Derive v1's keys from the claim list, and verify every quoted passage.

    A quote that is not in the context (after whitespace and case folding) is
    marked; the reviewer cannot invent evidence unnoticed."""
    ctx = _norm(context)
    claims = [c for c in verdict.get("claims") or [] if isinstance(c, dict)]
    for c in claims:
        ev = _norm(c.get("evidence") or "")
        c["evidence_found"] = bool(ev) and (ev in ctx or ev[:80] in ctx)
    verdict["unsupported_claims"] = [c.get("claim") for c in claims if c.get("status") == "not_in_context"]
    verdict["wrong_source_claims"] = [c.get("claim") for c in claims if c.get("status") == "wrong_source"]
    verdict["quotes_checked"] = sum(1 for c in claims if c.get("status") in ("supported", "wrong_source"))
    verdict["quotes_not_found"] = sum(1 for c in claims if c.get("status") in ("supported", "wrong_source")
                                      and not c["evidence_found"])
    return verdict


def _bodies(wanted: set[str]) -> dict[str, dict]:
    out = {}
    path = BENCH / "bodies.jsonl"
    if path.exists():
        for line in path.open():
            row = json.loads(line)
            if row["call_id"] in wanted:
                out[row["call_id"]] = row
    return out


def _render(messages: list[dict] | None) -> str:
    if not messages:
        return "(nothing: no model call was made, e.g. the system refused before generating)"
    parts = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, str):
            content = json.dumps(content)
        if m.get("tool_calls"):
            content = (content or "") + "\n[tool calls] " + json.dumps(m["tool_calls"])
        parts.append(f"[{m.get('role')}]\n{content}")
    text = "\n\n".join(parts)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS] + "\n[... context cut for length ...]"
    return text


def _call(http: httpx.Client, prompt: str) -> dict:
    body = {"model": MODEL, "temperature": 0, "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}]}
    for attempt in range(30):
        try:
            r = http.post(f"{BASE}/chat/completions", json=body)
        except httpx.HTTPError:
            time.sleep(10)
            continue
        if r.status_code == 200:
            text = r.json()["choices"][0]["message"]["content"] or ""
            text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
            try:
                return {"verdict": json.loads(text), "usage": r.json().get("usage")}
            except json.JSONDecodeError:
                if attempt >= 2:
                    return {"verdict": None, "error": f"unparseable: {text[:300]}"}
                continue
        if r.status_code == 429 and "PerDay" in r.text:
            print(f"\nREVIEWER DAILY QUOTA EXHAUSTED ({MODEL}); re-run later to resume.", flush=True)
            sys.exit(3)
        if r.status_code in (429, 500, 502, 503, 504):
            m = re.search(r'"retryDelay":\s*"(\d+)', r.text)
            time.sleep(float(m.group(1)) + 1 if m else min(15 * (attempt + 1), 120))
            continue
        return {"verdict": None, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    return {"verdict": None, "error": "reviewer unavailable after 30 attempts"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", default=None, help="only this system (default: every joined file)")
    ap.add_argument("--version", choices=("v1", "v2"), default="v1")
    ap.add_argument("--sample", choices=("handcheck",), default=None,
                    help="only the run-1 answers listed in handcheck.csv (to validate a prompt cheaply)")
    ap.add_argument("--key-env", default="GEMINI_API_KEY",
                    help="env var holding the Gemini key; lets two systems be reviewed in parallel on separate quotas")
    args = ap.parse_args()
    questions = {q["question_id"]: q for q in map(json.loads, (BENCH / "pilot_questions.jsonl").open())}
    http = httpx.Client(timeout=300, headers={"Authorization": f"Bearer {os.environ[args.key_env]}"})
    out_dir = BENCH / ("reviews" if args.version == "v1" else "reviews_v2")
    out_dir.mkdir(exist_ok=True)
    template = PROMPT if args.version == "v1" else PROMPT_V2
    sample = None
    if args.sample:
        import csv
        sample = {(r["system"], r["question_id"]) for r in csv.DictReader((BENCH / "handcheck.csv").open())}

    for joined in sorted((BENCH / "runs").glob("*.joined.jsonl")):
        system = joined.name.removesuffix(".joined.jsonl")
        if args.system and system != args.system:
            continue
        recs = [json.loads(line) for line in joined.open()]
        out = out_dir / f"{system}.jsonl"
        done = set()
        if out.exists():
            done = {(r["question_id"], r["run"]) for r in map(json.loads, out.open()) if r.get("verdict")}
        todo = [r for r in recs if (r["question_id"], r["run"]) not in done]
        if sample is not None:
            todo = [r for r in todo if r["run"] == 1 and (system, r["question_id"]) in sample]
        bodies = _bodies({r["answer_call_id"] for r in todo if r.get("answer_call_id")})
        for i, rec in enumerate(todo, 1):
            q = questions[rec["question_id"]]
            body = bodies.get(rec.get("answer_call_id") or "")
            context = _render(body and body["messages"])
            prompt = template.format(
                question=q["question"], gold=q["gold_answer"],
                facts="\n".join(f"{n}. {f}" for n, f in enumerate(q["answer_facts"], 1)),
                answer=rec["answer"] or "(empty answer)",
                context=context,
            )
            res = _call(http, prompt)
            if args.version == "v2" and res.get("verdict"):
                res["verdict"] = _check_v2(res["verdict"], context)
            row = {"question_id": rec["question_id"], "run": rec["run"], "reviewer": MODEL, "prompt": args.version,
                   "context_found": body is not None, **res}
            with out.open("a") as f:
                f.write(json.dumps(row) + "\n")
            v = res.get("verdict") or {}
            print(f"[review {system}] {i}/{len(todo)} {rec['question_id']} run{rec['run']} "
                  f"correct={v.get('correct')} refused={v.get('refused')} "
                  f"unsupported={len(v.get('unsupported_claims') or [])} {res.get('error') or ''}", flush=True)


if __name__ == "__main__":
    main()
