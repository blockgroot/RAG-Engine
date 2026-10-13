"""What reaches the answer model, without paying for an answer.

Runs Handbook's real retrieval for each question with a stand-in model that
records the grounded prompt and replies with a stub, then asks the reviewer
which of the question's required facts the prompt's CONTEXT holds, and which
expected documents made it in. A retrieval change is judged here first; only
a change that puts more facts in front of the model is worth an answer run.

Run: .venv/bin/python -m evaluation.erb.context_check --only ids.txt --tag base [--deep-broad]
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env.bench", override=True)

BENCH = Path(os.getenv("BENCH_DIR", "evaluation/reports/bench3-haiku"))

JUDGE = """Below is the CONTEXT a question-answering system retrieved, and a list of REQUIRED FACTS.
For each fact, say whether the CONTEXT states it (same meaning, any wording; a fact
that is only partly stated counts as false).

REQUIRED FACTS:
{facts}

<<<CONTEXT
{context}
CONTEXT>>>

Return ONLY a JSON object: {{"present": [true/false, one per fact in order]}}"""


class _Recorder:
    """Keeps the grounded prompt and stubs its answer; every other call (the
    question split, tone) goes to a real model, because a stub there changes
    retrieval: "MODE: A / Stub answer" parsed as two sub-questions."""

    def __init__(self, http) -> None:
        self.prompts: list[str] = []
        self._http = http

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.prompts.append(prompt)
        if "CONTEXT:\n<<<UNTRUSTED_DOCUMENT_CONTENT>>>" in prompt:
            return "MODE: A\n\nStub answer [1]."
        body = {"model": "gemma4:31b", "temperature": 0, "messages": [{"role": "user", "content": prompt}]}
        return self._http.post("https://ollama.com/v1/chat/completions", json=body).json()["choices"][0]["message"]["content"]

    def __getattr__(self, name):  # any other provider attribute (model name, ...)
        return None


def _context(prompt: str) -> str:
    # The policy above the fence names the markers too, so take the block after "CONTEXT:".
    m = re.search(r"CONTEXT:\s*<<<UNTRUSTED_DOCUMENT_CONTENT>>>(.*?)<<<END_UNTRUSTED_DOCUMENT_CONTENT>>>", prompt, re.S)
    return m.group(1) if m else ""


def main() -> None:
    import httpx

    from .run_bench import Handbook, _broad, _dsids

    ap = argparse.ArgumentParser()
    ap.add_argument("--only", type=Path, required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--deep-broad", action="store_true")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = ap.parse_args()
    for kv in args.set:
        k, _, v = kv.partition("=")
        os.environ[k] = v

    wanted = args.only.read_text().split()
    questions = {q["question_id"]: q for q in map(json.loads, (BENCH / "pilot_questions.jsonl").open())}
    hb = Handbook()
    judge = httpx.Client(timeout=300, headers={"Authorization": f"Bearer {os.environ['OLLAMA_API_KEY']}"})
    rec = _Recorder(judge)
    hb.pipeline._llm = rec
    hb.pipeline._llm_aux = rec
    out = BENCH / "context_checks" / f"{args.tag}.jsonl"
    out.parent.mkdir(exist_ok=True)
    out.write_text("")
    for qid in wanted:
        q = questions[qid]
        if args.deep_broad:
            from app.rag.deep import use_deep_read

            use_deep_read(_broad(q))
        rec.prompts.clear()
        for attempt in range(5):  # Jina's free tier answers a burst with 429
            try:
                r = hb.pipeline.answer(q["question"], hb.org_id)
                break
            except Exception:  # noqa: BLE001
                if attempt == 4:
                    raise
                import time

                time.sleep(30)
        grounded = [p for p in rec.prompts if "<<<UNTRUSTED_DOCUMENT_CONTENT>>>" in p]
        ctx = _context(grounded[0]) if grounded else ""
        # Blocks are "[n] (From: ...)" in hit order and the budget keeps a prefix,
        # so the first k hits are the ones the model saw.
        in_ctx = r.sources[: len(re.findall(r"^\[\d+\] \(From: ", ctx, re.M))]
        docs = set(_dsids([h.document_id for h in in_ctx]))
        gold = set(q["expected_doc_ids"])
        present = []
        if ctx:
            body = {"model": "gemma4:31b", "temperature": 0, "response_format": {"type": "json_object"},
                    "messages": [{"role": "user", "content": JUDGE.format(
                        facts="\n".join(f"{i}. {f}" for i, f in enumerate(q["answer_facts"], 1)), context=ctx)}]}
            text = judge.post("https://ollama.com/v1/chat/completions", json=body).json()["choices"][0]["message"]["content"]
            present = json.loads(re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()).get("present", [])
        row = {"question_id": qid, "type": q["question_type"], "deep": bool(args.deep_broad and _broad(q)),
               "context_chars": len(ctx), "docs_in_context": len(docs), "gold_docs": len(gold),
               "gold_in_context": len(gold & docs), "facts": len(q["answer_facts"]), "facts_in_context": sum(map(bool, present))}
        with out.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{qid} {q['question_type'][:12]:12} docs={len(docs):2} chars={len(ctx):6} "
              f"gold={len(gold & docs)}/{len(gold)} facts={row['facts_in_context']}/{row['facts']}", flush=True)


if __name__ == "__main__":
    main()
