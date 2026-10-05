"""Benchmark 1 runner: ask the frozen pilot questions to one system (plan §8).

Systems:
  handbook  RagPipeline.answer, core mode (no tool pinned), single turn.
  basic     Plain vector search over the SAME Handbook index, top 10 chunks,
            EnterpriseRAG-Bench's own baseline answer prompt, one LLM call.
            (Their shipped baseline sends 10 FULL documents, ~12-15k tokens,
            which Groq's free 8k-tokens-per-minute cap refuses per request.)
  onyx      POST /chat/send-chat-message, stream=false, deep_research=false.

Rules carried out here: one question at a time, a 5 s gap so background calls
land in their own window, every raw answer kept, resumable (a crash never
re-spends quota on finished questions). Tokens are NOT counted here — the proxy
log is the only token source; ``join_tokens.py`` matches it to these windows.

Run: .venv/bin/python -m evaluation.erb.run_bench --system handbook --split test --runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(".env.bench", override=True)

MANIFEST = Path("evaluation/reports/bench1/manifest.json")
OUT = Path("evaluation/reports/bench1/runs")
GAP_SECONDS = 5.0


def _questions(data: Path, split: str) -> list[dict]:
    manifest = json.loads(MANIFEST.read_text())
    wanted = {q["question_id"]: q["split"] for q in manifest["questions"]}
    rows = [json.loads(line) for line in (data / "questions.jsonl").open()]
    return [q for q in rows if wanted.get(q["question_id"]) in ({"dev", "test"} if split == "all" else {split})]


def _dsids(document_ids: list[str]) -> list[str]:
    """Row ids of the chunks sent to the model -> benchmark dsids, in prompt order.
    Search hits carry ``documents.id``, not ``source_external_id``."""
    from app.db import get_connection

    order = list(dict.fromkeys(document_ids))
    if not order:
        return []
    with get_connection() as conn:
        rows = dict(conn.execute(
            "SELECT id::text, source_external_id FROM documents WHERE id = ANY(%s::uuid[])", (order,)
        ).fetchall())
    return [rows[d] for d in order if rows.get(d)]


# --------------------------------------------------------------------------- systems
class Handbook:
    def __init__(self) -> None:
        from dataclasses import replace

        from app.config.settings import RagSettings, WorkspaceAgentSettings
        from app.rag.factory import build_rag_pipeline
        from app.rag.prompts import WORKSPACE_PROMPT_PROFILE

        from .load_handbook import bench_org

        settings = replace(RagSettings.from_env(), fallback_response=WorkspaceAgentSettings.from_env().fallback_response)
        self.pipeline = build_rag_pipeline(
            settings=settings, prompt_profile=WORKSPACE_PROMPT_PROFILE, memory=None, web_search=None
        )
        from app.vectorstore import build_vector_store

        self.org_id = bench_org(build_vector_store())

    def ask(self, question: str) -> dict:
        r = self.pipeline.answer(question, self.org_id)
        doc_ids = _dsids([h.document_id for h in r.sources])
        return {
            "answer": r.answer,
            "document_ids": doc_ids,
            "refused": not r.answered,
            "raw": {
                "source": r.source,
                "top_score": r.top_score,
                "recovery_used": r.recovery_used,
                "n_chunks": len(r.sources),
                "context_chars": sum(len(h.content) for h in r.sources),
            },
        }


class Basic:
    TOP_K = 10

    def __init__(self) -> None:
        import httpx

        from app.embeddings import build_embedding_provider
        from app.vectorstore import build_vector_store

        from .load_handbook import bench_org

        self.store, self.embedder = build_vector_store(), build_embedding_provider()
        self.org_id = bench_org(self.store)
        self.http = httpx.Client(timeout=300)
        self.prompt = (Path(__file__).parent / "erb_answer_prompt.txt").read_text()

    def ask(self, question: str) -> dict:
        hits = self.store.query(self.org_id, self.embedder.embed([question])[0], top_k=self.TOP_K)
        context = "\n\n".join(f"Document: {h.document_title}\n{h.content}" for h in hits)
        resp = self.http.post(
            "http://localhost:4000/v1/chat/completions",
            headers={"X-Bench-Client": "basic"},
            json={"model": "bench-answer", "messages": [
                {"role": "user", "content": self.prompt.format(context_documents=context, question=question)}
            ]},
        ).json()
        answer = resp["choices"][0]["message"]["content"].strip() if resp.get("choices") else ""
        return {
            "answer": answer,
            "document_ids": _dsids([h.document_id for h in hits]),
            "refused": None,  # decided by the judge's bucket mapping, not here
            "raw": {"n_chunks": len(hits), "context_chars": len(context), "error": resp.get("error")},
        }


class Onyx:
    def __init__(self) -> None:
        import httpx

        self.base = os.environ.get("ONYX_URL", "http://localhost:3000/api").rstrip("/")
        self.http = httpx.Client(timeout=600)
        key = os.environ.get("ONYX_API_KEY")
        if key:
            self.http.headers["Authorization"] = f"Bearer {key}"
        else:
            r = self.http.post(f"{self.base}/auth/login", data={
                "username": os.environ["ONYX_ADMIN_EMAIL"], "password": os.environ["ONYX_ADMIN_PASSWORD"]})
            r.raise_for_status()

    FORCE_SEARCH = False

    def ask(self, question: str) -> dict:
        search_tool = int(os.environ.get("ONYX_SEARCH_TOOL_ID", "1"))
        body = {
            "message": question,
            "stream": False,
            "deep_research": False,
            # Internal search only (our web search is off too). Allowed, not
            # forced: whether to search is Onyx's own decision, as shipped.
            "allowed_tool_ids": [search_tool],
            "chat_session_info": {"persona_id": int(os.environ.get("ONYX_PERSONA_ID", "0"))},
        }
        if self.FORCE_SEARCH:
            body["forced_tool_id"] = search_tool
        r = self.http.post(f"{self.base}/chat/send-chat-message", json=body)
        data = r.json()
        if r.status_code != 200:
            return {"answer": "", "document_ids": [], "refused": None, "raw": {"http": r.status_code, "error": data}}
        return {
            "answer": data.get("answer_citationless") or data.get("answer") or "",
            "document_ids": list(dict.fromkeys(d["document_id"] for d in data.get("top_documents") or [])),
            "refused": None,
            "raw": {
                "tool_calls": [t.get("tool_name") for t in data.get("tool_calls") or []],
                "cited": [c.get("document_id") for c in data.get("citation_info") or []],
                "n_top_documents": len(data.get("top_documents") or []),
                "error_msg": data.get("error_msg"),
                "chat_session_id": str(data.get("chat_session_id")),
            },
        }


class OnyxForcedSearch(Onyx):
    """Onyx with its search tool forced on every question — the variant that
    always retrieves, like Handbook does. Reported beside Onyx as shipped."""

    FORCE_SEARCH = True


SYSTEMS = {"handbook": Handbook, "basic": Basic, "onyx": Onyx, "onyx-forced": OnyxForcedSearch}


# --------------------------------------------------------------------------- loop
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", choices=SYSTEMS, required=True)
    ap.add_argument("--split", choices=["dev", "test", "all"], required=True)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--data", type=Path, default=Path("~/Desktop/bench1-data"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tag", default="", help="variant label, e.g. topk3 (kept in the file name)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="setting for this variant only, e.g. RAG_TOP_K=3 (wins over .env.bench)")
    args = ap.parse_args()
    for kv in args.set:
        key, _, value = kv.partition("=")
        os.environ[key] = value

    questions = _questions(args.data.expanduser(), args.split)[: args.limit]
    system = SYSTEMS[args.system]()
    name = args.system + (f"-{args.tag}" if args.tag else "")
    records = OUT / f"{name}.records.jsonl"
    records.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if records.exists():
        done = {(r["question_id"], r["run"]) for r in map(json.loads, records.open())}

    for run in range(1, args.runs + 1):
        for i, q in enumerate(questions, 1):
            if (q["question_id"], run) in done:
                continue
            t0 = time.time()
            try:
                out = system.ask(q["question"])
            except Exception as exc:  # noqa: BLE001 - a failed question is a recorded result, not a crash
                out = {"answer": "", "document_ids": [], "refused": None, "raw": {"exception": repr(exc)[:500]}}
            t1 = time.time()
            row = {
                "system": name, "question_id": q["question_id"], "question_type": q["question_type"],
                "run": run, "q_start": t0, "q_end": t1, "seconds": round(t1 - t0, 2), **out,
            }
            with records.open("a") as f:
                f.write(json.dumps(row) + "\n")
            print(f"[{name} run {run}] {i}/{len(questions)} {q['question_id']} {t1 - t0:.1f}s "
                  f"docs={len(out['document_ids'])} {'ERR' if 'exception' in out['raw'] or 'error' in out['raw'] and out['raw']['error'] else ''}",
                  flush=True)
            time.sleep(GAP_SECONDS)


if __name__ == "__main__":
    main()
