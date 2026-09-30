# Open-Ended Charts Plan

> **Status (2026-09-30): Phases 1, 2 and 3 BUILT** (`app/insights/query.py`,
> `activity_facts.attrs`, `app/doctables` + `app/insights/tables.py`; tests
> `test_insights_query.py`, `test_insights_attrs.py`, `test_doctables.py`).
> Phase 3 v1 reads Drive Sheets (first tab), CSVs, pipe tables in Notion/Docs
> and Word tables; XLSX, PDF tables and multi-tab sheets are not read yet.
> Phase 4 is still plan only. The shipped chart system is described in
> `docs/plans/2026-09-02-visual-representation.md` and CLAUDE.md §3 "Visual
> Representation".

**Goal:** A member can ask for a chart of anything the company's connected data
can actually count — not only the 12 metrics someone wrote down in advance —
including data that lives INSIDE a document (a sales sheet, a budget table),
and still get an interactive `Chart.tsx` chart whose every number came from
stored rows.

**Architecture in one line:** widen what the model may ASK FOR (a validated
query grammar instead of a menu) and what we STORE (all fields a source already
returns, plus tables found in documents) — never let the model compute.

---

## 1. How it works today

```
Sync (ingest worker / facts-only GitHub branch)
  └─► activity_facts rows: provider, kind, actor, subject, state,
      occurred_at, value, url, external_id           (schema.sql:744)

Ask "chart PRs merged per week"
  └─► classify_question / resolve.py
        └─► model picks ONE key from registry.METRICS (12 today)
              └─► store.run_metric: fixed aggregate + whitelisted group_by
                    └─► points ──► Chart.tsx (hand-rolled SVG, hover rows)
```

The 12 metrics (`app/insights/registry.py`): `docs_changed` (Notion),
`drive_docs_changed` (Drive), `prs_opened`, `prs_merged`, `pr_reviewers`,
`pr_lead_time`, `commits_by_author` (GitHub), `issues_completed`,
`issue_states`, `issue_cycle_time` (Linear), `slack_threads` (Slack),
`sentiment_by_theme` (Forms).

**The invariant (kept by this plan):** numbers come from SQL over rows we
stored; the LLM never produces a number, an axis or a date.

## 2. The problem — three different failures

| # | Failure | Example | Root cause |
|---|---|---|---|
| A | **Combination not on the menu** | "PRs merged per repo per week, split by person" | A `Metric` is a fixed (kind, dims) pair; the model can only select one. The rows exist. |
| B | **Field never captured** | "PRs by label", "issues by estimate", "PR size" | `activity_facts` has one fixed shape; everything else the API returned is dropped at sync. |
| C | **The document IS the data** | "chart last year's sales by region" from a sales sheet | Drive skips `application/vnd.google-apps.spreadsheet`; CSV exists only as attachment text; tables in PDFs/Notion become chunk text. No cell is ever stored as a number. |

Adding more metrics fixes A slowly, B partly, C never.

## 3. Research — how production systems do it

Gathered 2026-09-30 from web search summaries (the egress proxy blocked full
page fetches of docs.onyx.app, docs.thoughtspot.com, developers.databricks.com
and arxiv.org, so claims below are as summarised, not read end to end).

**Pattern 1 — semantic layer + constrained query (model never writes SQL)**
- **ThoughtSpot Spotter / AgentQL**: model emits a proprietary semantic query
  dialect over a modelled layer (TML); a deterministic compiler produces the
  SQL — explicitly "instead of relying on Python-based code execution".
  https://docs.thoughtspot.com/cloud/26.9.0.cl/agentql ·
  https://thoughtspot.com/blog/spotter-semantics
- **Cube AI API**: returns a Cube REST query (measures/dimensions/filters JSON)
  and "does not generate raw SQL"; constrained input makes a "technically valid
  hallucination" much less likely.
  https://cube.dev/blog/a-practical-guide-to-getting-started-with-cubes-ai-api
- **Snowflake Cortex Analyst**: agentic system + semantic model, "90%+ SQL
  accuracy on real-world use cases", ~2x single-prompt GPT-4o text-to-SQL.
  https://www.snowflake.com/en/blog/engineering/cortex-analyst-text-to-sql-accuracy-bi/
- **Databricks Genie**: text-to-SQL grounded in catalog metadata, plus
  **trusted assets** — author-verified parameterised queries; an answer that
  used one is badged "Trusted".
  https://docs.databricks.com/aws/en/genie/concepts

**Pattern 2 — code interpreter (model writes Python in a sandbox)**
- **ChatGPT Data Analysis**: pandas/matplotlib in a sandbox; bar/line/pie/
  scatter interactive, other shapes static images.
  https://help.openai.com/en/articles/8437071-data-analysis-with-chatgpt
- **Onyx code interpreter**: sandboxed Python for CSV/spreadsheet analysis and
  charts in the chat UI.
  https://docs.onyx.app/overview/core_features/code_interpreter.md
- **Microsoft LIDA** (ACL 2023): summarizer → goal explorer → viz code
  generator (execute + filter) → infographer.
  https://www.microsoft.com/en-us/research/project/lida/

**Pattern 3 — tables extracted as structure, not flattened to text**
- **TabRAG** (arXiv 2511.06582): serialising tables to linear text loses 2-D
  structure and causes hallucinated/imprecise answers; parse into structured
  representations instead. https://arxiv.org/pdf/2511.06582v2
- **Docling** (IBM): PDF/DOCX/PPTX/XLSX → structured JSON; TableFormer
  reported 93.6% table accuracy vs Tabula 67.9%, Camelot 73.0%.
  https://arxiv.org/pdf/2501.17887
- **DuckDB / FlockMTL**: structured tables + RAG in one engine.
  https://duckdb.org/library/beyond-quacking-flockmtl/

**Model-written chart specs** (Vega-Lite on nvBench, arXiv 2401.11255):
dominant failures are misunderstood data attributes and grammar errors — the
model is fine at choosing a SHAPE, not at producing the data.

**Takeaway:** every system that promises correct numbers (ThoughtSpot, Cube,
Cortex, Genie trusted assets) keeps our invariant. The difference from us is
that they give the model a GRAMMAR, not a MENU.

## 4. Decisions

- **D1 — Query grammar replaces metric selection.** The model returns a
  validated JSON query; `store` compiles it. Existing metrics become
  **trusted presets** (Genie's pattern), offered first and badged.
- **D2 — Keep every field the source already returns** (`activity_facts.attrs
  JSONB`), zero extra API calls, same rule as Second Brain capture.
- **D3 — Tables in documents are stored as rows** (`doc_tables` +
  `doc_table_rows`), found by normal retrieval, queried by the same grammar.
- **D4 — Prose numbers are the LAST, lowest-trust path**, verified verbatim
  against the chunk (the `security/links.py` provenance idea).
- **D5 — The knowledge graph resolves entities, it does not hold numbers.**
  Used for filter resolution ("Sana", "the auth repo") and relationship charts
  (edge counts through `walk.py`'s visibility filter).
- **Rejected:**
  - *Image generation* — slow, unverifiable, not interactive.
  - *Python sandbox over the shared index* — tenant data inside model-written
    code, non-deterministic, heavy for a 512MB box. Revisit for a member's OWN
    attachments only.
  - *Raw text-to-SQL on Postgres* — puts tenant isolation and the visibility
    predicate in the model's hands; ~2x worse than a semantic layer per Cortex.
  - *Vega-Lite* — `Chart.tsx` already draws every shape we need; change the
    data source, not the renderer.

## 5. Phases

### Phase 1 — Query grammar over `activity_facts` (fixes A)

Smallest change, most questions unblocked; nothing new stored.

```json
{"source": "activity", "provider": "github", "kind": "pr_merged",
 "measure": {"op": "count"},
 "group_by": ["subject", "actor"], "period": "week", "days": 90,
 "filters": {"subject": "chain-guard"}}
```

- `measure.op` ∈ closed set: `count`, `count_distinct` (actor | subject),
  `sum`/`avg`/`min`/`max` over `value`.
- `group_by`: ≤2 entries from `registry.DIMENSIONS` (still identifiers from a
  whitelist, never caller text); `period` from `PERIODS`.
- `filters`: values bound as `%(…)s`, resolved against `list_subjects`-style
  lookups exactly like today's `focus`.
- `kind` must be one the scope actually has rows for (offer only those).
- Tasks:
  1. `app/insights/query.py` — `ChartQuery` frozen dataclass + `validate()`
     (raises, never sanitises — the `run_metric` posture).
  2. `store.run_query(q, org_id, workspace_id, viewer)` — compiler reusing
     `_scoped`, `_floored` (sentiment floor), `FINER_PERIOD` refinement.
  3. Registry metrics re-expressed as preset `ChartQuery`s; `run_metric`
     becomes a thin wrapper so pins and panels keep working.
  4. `resolve.py` prompt: offer presets first, then the grammar; validation is
     still the gate (a test assumes the prompt LOST).
  5. Pins store a `ChartQuery` (validated on write, as today).
- Tests: grammar fuzz (no identifier from input reaches SQL), every preset
  byte-identical to the old metric output, scope/space isolation, suppression
  floor still applied on `forms`.

### Phase 2 — Capture all returned attributes (fixes B)

- `ALTER TABLE activity_facts ADD COLUMN IF NOT EXISTS attrs JSONB` (after the
  CREATE TABLE in `schema.sql`), `DEFAULT '{}'`.
- Connectors fill it from payloads already fetched: GitHub labels, additions,
  deletions, draft; Linear estimate, priority, labels, cycle; Slack reply
  count. **Zero new API calls** — the fakes in `tests/test_*_source.py` and
  `test_github_facts.py` must reject any new URL.
- Each provider declares `ATTR_DIMENSIONS` / `ATTR_MEASURES` (key → type);
  the grammar accepts `attrs.<key>` only from that declaration and compiles it
  to `attrs->>%(key)s` with a bound key (a value, not an identifier).
- Fallback for a field never captured: a live GitHub/Linear read returns rows
  in the same shape and the same compiler aggregates them in Python —
  bounded and `truncated` like every live read.
- Honest ceiling: facts written before Phase 2 have `attrs = '{}'`; a chart
  over an attr says "measured since <first attrs row>" (the `first_fact_at`
  pattern).

### Phase 3 — Document tables (fixes C — the sales sheet)

- New package `app/doctables/` (orchestrator over existing interfaces — no
  `base.py`, per §2): `extract.py`, `profile.py`, `store.py`.
- **Extraction at ingest**, per source:
  - Drive Sheets: export as XLSX/CSV (we already fetch the file); stop
    skipping the spreadsheet MIME **for tables only** — still no chunk
    embedding of cells.
  - Drive/attachment CSV/TSV: reuse `attachments/extract.py`'s CSV reader.
  - Notion/Markdown tables: parse table blocks / pipe tables.
  - PDF/DOCX: Docling, lazily imported, behind `DOCTABLES_DOCLING_ENABLED`
    (heavy; measure RSS on the 512MB box first).
- **Tables:**
  - `doc_tables(id, org_id, workspace_id, document_id → documents ON DELETE
    CASCADE, name, columns JSONB, row_count, truncated, created_at)`
  - `doc_table_rows(table_id, row_no, cells JSONB)`
  - Bounded: `DOCTABLES_MAX_ROWS`, `DOCTABLES_MAX_COLS`; truncation stored and
    shown.
- **Profiling** (LIDA's summarizer, but deterministic): column type
  (number/date/category/text), unit/currency sniffed from header + cells,
  distinct count, sample values. No model call.
- **Access:** a table joins its `documents` row, so `visibility_predicate` and
  the org/space pin apply unchanged. No second ACL.
- **Finding the table:** one chunk per table — its name, columns, types,
  units, row count — embedded like any chunk, so the 0.35 gate, routing and
  document-level access all apply.
- **Question time:** retrieval hits a table chunk → the grammar with
  `"source": "table", "table_id": …` and column names validated against that
  table's profiled `columns` → compiled to SQL over `cells->>%(col)s` with
  typed casts (a failed cast = excluded cell, counted and disclosed) → points.
  Each point carries the `row_no`s it aggregated so the hover shows real cells.
- Re-ingest replaces a document's tables whole (delete + insert), same as
  chunks.

### Phase 4 — Numbers stated in prose (optional)

Only if Phase 3 leaves real questions unanswered.
- Model extracts `(label, value, unit, quoted_span)` from retrieved chunks.
- Code keeps a value only if `quoted_span` occurs verbatim in a chunk the
  viewer may read AND contains `value`. Anything else is dropped.
- Chart labelled "extracted from text · N sources"; each point cites its chunk.
- Never cached, never pinned (it is not a stored measurement).

### Graph usage (alongside Phases 1–3)

- Filter resolution: "Sana's PRs" → her identity keys via `app/graph/`,
  "auth repo" → a repo entity.
- Relationship charts: edge counts (e.g. reviews between people) through
  `walk.py`'s visibility filter — only once `GRAPH_RETRIEVAL_ENABLED` has
  passed `evaluation.graph_eval`.

## 6. What stays the same

- The model never produces a number, an axis or a date.
- Every identifier reaching SQL comes from a whitelist; every value is bound.
- Scope isolation (`_scoped`), space 403s, sentiment floor + owners-only.
- `Chart.tsx`, hover details, empty-state captions, `points: null` vs `[]`.
- Charts must be asked for in chat (`resolve._wants_a_chart`).

## 7. Order and size

| Phase | Scope | Size | Unblocks |
|---|---|---|---|
| 1 | `app/insights/` only | Small | Any combination of stored facts |
| 2 | 1 column + connector capture | Medium | Labels, estimates, sizes… |
| 3 | New package + 2 tables + ingest | Largest | Sheets / CSV / doc tables |
| 4 | Extraction + verification | Medium | Numbers in prose |

## 8. Open questions

- Phase 3 engine: Postgres over JSONB `cells` (no new dependency) vs
  in-process DuckDB (faster pivots, new dependency). Default: Postgres until a
  measured table is too slow.
- Should Drive Sheets also be chunk-indexed for Q&A? Current rule says no
  (numbers from chunk text are unfalsifiable); table rows may answer "what was
  March revenue?" better than chunks would.
- Docling memory footprint on Render free — measure before enabling.
- Multi-table joins (sales sheet × targets sheet): out of scope for v1.
