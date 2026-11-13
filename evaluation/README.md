# Golden-set evaluation (Phase 7)

> This file describes the regression harness, not the product. What Handbook does today is
> [PRODUCT_STATUS.md](../docs/handbook/PRODUCT_STATUS.md). The cases below still use the original policy
> corpus so a retrieval or grounding change fails loudly.

A small, deliberate regression suite for the **Policy Agent** — a tripwire that
fails loudly when a change quietly breaks retrieval, grounding, the fallback, the
web-search path, or conversation memory. It is **not** a benchmark; 29 hand-picked
cases beat a large fuzzy set for a regression gate you trust.

## What it checks

The set (`golden_set.py`) has four categories, one per path the agent can take:

| Category       | # | Must do                                              | Scored by |
|----------------|---|------------------------------------------------------|-----------|
| `answerable`   | 20| answer from policy docs, with the expected facts     | path check **+ RAGAS** |
| `fallback`     | 6 | return the fixed internal fallback (nothing in docs) | path check |
| `web`          | 2 | trip the web-search tool for an external entity      | path check (network) |
| `conversation` | 1 | rewrite a follow-up via memory, retrieve right chunk | path check **+ RAGAS** |

The corpus is the **real "Acme HR Policies" Notion data** (Phases 4–6) reproduced
inline, so the eval seeds its own org and is fully deterministic — it runs the same
on a laptop and in CI (which cannot reach Notion). See the module docstring for the
per-case rationale.

## Two tiers, and why (cost vs coverage)

RAGAS scores with an **LLM-as-judge**: several judge calls *per metric per case*.
Running all four metrics on every push would multiply LLM cost for little marginal
signal, so we split the eval by cadence:

- **Fast tier — every push / PR.** The **path-firing** checks
  (`tests/test_golden_set.py`): one LLM *generation* per case, then plain
  deterministic assertions — did the right path fire, are the known facts present,
  did the follow-up get rewritten. Cheap, fast, and the actual build gate: it fails
  if a known-answerable question stops being answered, a known-unanswerable one
  stops falling back, or conversation/grounding regress. Web cases are excluded here
  (`-m "not network"`) because DuckDuckGo rate-limits and a flaky external search
  must never redden an unrelated push.
- **RAGAS tier — nightly + manual.** `run_eval --ragas` scores faithfulness /
  answer relevancy / context precision / recall on the answerable cases and fails if
  a mean drops below the baseline in `ragas_scoring.py::BASELINE`. Nightly is the
  right cadence for a slow-moving quality signal; you can also trigger it by hand
  (`workflow_dispatch`) before a risky merge.

This is a genuine tradeoff, not an obvious answer: the fast tier trades some
depth (it checks *that* the answer is right on known facts, not *how faithful*
free-form answers are) for speed and determinism; the RAGAS tier adds that depth
at real cost, less often. Both keep to the project's $0 / self-hostable principle
— RAGAS is pointed at our own LLM endpoint and local BGE-M3 embeddings, never a
paid judge API.

## Running it

```bash
# Fast tier (deterministic path-firing), as CI runs it:
pytest -q -m "not network"                 # includes tests/test_golden_set.py

# Full report incl. gate behaviour (Part 3 input), no RAGAS:
python -m evaluation.run_eval              # -> evaluation/reports/latest.md

# Add RAGAS scoring (needs the eval extra):
pip install -e ".[eval]"
python -m evaluation.run_eval --ragas
python -m evaluation.run_eval --ragas --skip-web   # what the nightly CI runs
```

Prerequisites are the same as the test suite: `DATABASE_URL` (Postgres+pgvector)
and a configured LLM (`LLM_ADAPTER` / `LLM_MODEL` / `LLM_API_KEY`, see `.env.example`).
In CI the LLM values (`LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL`) come from repository
**secrets**; without them only the no-LLM checks run — see `.github/workflows/eval.yml`.
The nightly job also runs the prompt-injection probe (`scripts/probe_injection.py --fail-on-leak`).

## The report

`report.py` renders `evaluation/reports/latest.md`: the path-firing table, a
**confidence-gate behaviour** section (per-case `top_score` vs the 0.35 threshold,
and whether the gate or the strict prompt did the refusing), the RAGAS table when
run, and per-case detail. That gate section is the evidence base for the Phase 7
**Part 3** gate analysis (`evaluation/reports/GATE_FINDINGS.md`).

> Note: the harness ingests the corpus plainly (preprocess → chunk → embed), without
> the Phase 6 contextual-prefix LLM step, so runs are deterministic and fast. The
> hybrid + rerank + gate *retrieval* path is exercised in full; only the ingest-time
> context prefix is skipped.

## Other suites in this folder

Run by hand unless noted. Each module's docstring says how.

- `retrieval_eval.py` — retrieval only, no LLM: the rank of the right chunk after
  fusion and rerank (`tests/test_retrieval_eval.py`, run in CI).
- `graph_eval.py` — multi-hop retrieval with the knowledge graph off and on.
- `agent_scenarios.py` — Agent mode requests against a real model and real Linear and
  GitHub tool definitions; writes are never sent. `linear_signin.py` signs the bench in.
- `agentbench/`, `agentbench2/` — Agent mode benchmarks that write into sandbox tools and
  score the difference before and after, field by field.
- `erb/` — the frozen retrieval-and-answer benchmark: question set, corpus, loaders.
- `sales_e2e/`, `recruit_e2e/` — spreadsheet questions (Ask and Chart mode) asked through
  the real `/chat/stream` and checked against figures worked out from the rows.

Results of past runs are under `evaluation/reports/`.
