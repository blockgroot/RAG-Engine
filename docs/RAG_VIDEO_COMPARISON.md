# Production RAG checklist vs. this codebase

Source: https://www.youtube.com/watch?v=4KiiKQ9RVvA — 8 pointers for a
production-grade RAG system. Below: what we already have, what's partial,
what's missing entirely. Status is judged against CLAUDE.md §2/§4/§6, not
aspirational.

| # | Video pointer | Status | Where |
|---|---|---|---|
| 1 | Restructuring & smart chunking | ✅ Have | `app/ingestion/chunking.py`, `chunk_tokens.py` |
| 2 | Metadata: summaries/keywords/hypothetical questions | ⚠️ Partial | `app/ingestion/contextualize.py` |
| 3 | Relational + vector DB, hard filters | ⚠️ Partial | Postgres+pgvector, `org_id`/`workspace_id` filters only |
| 4 | Hybrid search | ✅ Have | `app/rag/retrieval.py`, `app/reranker/` |
| 5 | Reasoning engine / multi-agent | ⚠️ Partial | `app/agent/orchestration.py` (LangGraph, deterministic routing, no planner) |
| 6 | Validation layer (gatekeeper/auditor/strategist) | ⚠️ Partial | confidence gate + strict prompt only, no post-generation auditor |
| 7 | Evaluation metrics (quant/qual/perf) | ✅ Have | `evaluation/` + `rag.query_signals` |
| 8 | Stress testing & red teaming | ⚠️ Partial | injection scrub + probe script, no formal red-team suite |

---

## 1. Restructuring & smart chunking — ✅ mostly have it

**Have:** paragraph → sentence → word → hard character-cap splitting
(`chunking.py`), token-aware sizing via a calibrated heuristic estimator
(`chunk_tokens.py`, `CHUNK_TOKEN_BACKEND`), a character ceiling
(`CHUNK_MAX_CHARS`) that can't be fooled by base64/CJK/URLs. Notion block
structure is walked and converted to text in the adapter.

**Missing:** no layout-aware parsing of headings/tables as structural units —
"preprocessing scope" is deliberately plain text/Markdown (§4: "Layout-aware
extraction from PDF/DOCX/HTML... deliberately deferred"). A table split
mid-row isn't specially protected; chunking splits on paragraph/sentence
boundaries, not semantic sections. Google Docs export to Markdown gets the
same generic treatment.

**Verdict:** the safety net (never producing a broken/oversized chunk) is
stronger than most implementations. The "preserve structure" half (tables,
heading hierarchy as chunk boundaries) is genuinely not done.

## 2. Metadata creation — ⚠️ have contextualization, not the full set

**Have:** contextual retrieval (Anthropic's technique) — an LLM-generated
context sentence is prepended to each chunk before embedding
(`app/ingestion/contextualize.py`), bounded/deferred/concurrency-tuned for
free-tier LLM quotas.

**Missing:** no chunk-level keyword extraction, no stored summary field
separate from the context prefix, and no pre-generated hypothetical
questions per chunk. The video's "let a user query directly match a
precomputed question" trick isn't implemented — we rely on hybrid
search + reranking to bridge the gap instead.

**Verdict:** we solved the same problem (query-to-chunk matching) a different
way (better retrieval mechanics) rather than the video's way (richer
per-chunk metadata). Worth trying hypothetical-questions as a cheap
addition — it's ingest-time-only cost, same shape as contextualize.py.

## 3. Vector + relational DB integration — ⚠️ have the DB, not the filters

**Have:** Postgres + pgvector is the actual architecture (not a vector-only
store), `org_id`/`workspace_id` scoping is a real SQL `WHERE` clause (the
whole isolation model depends on this), relational joins already happen
(`documents` ↔ `chunks`, ingestion jobs, conversations).

**Missing:** no user-facing hard filters like date range or department/tag.
`documents`/`chunks` carry no department/category/date-range columns beyond
`source_provider`/`created_at`. A query can't say "policies updated after
March" or "only Engineering department docs" — retrieval is `org_id` (+
optional `workspace_id`) scoped only.

**Verdict:** the infrastructure for this is trivial to add (a `WHERE`
clause and a metadata column) — it's genuinely just not needed yet because
there's no per-doc metadata schema exposing department/date. Cheapest gap
to close on this list if a real need shows up.

## 4. Hybrid search — ✅ have it, thoroughly

**Have:** vector (cosine) + Postgres full-text (`ts_rank`/BM25-style) fused
with Reciprocal Rank Fusion (k=60), over-retrieval pool of 30 candidates,
cross-encoder reranking (`bge-reranker-v2-m3`). Real Okapi BM25 re-ranking
was later added on top of FTS-filtered candidates (Phase 18). Keyword search
capacity-bounded (`KEYWORD_CANDIDATE_LIMIT`) to avoid unbounded row transfer.

**Verdict:** this is the most complete pointer on the list — matches or
exceeds what the video describes. No gap.

## 5. Reasoning engine & multi-agent systems — ⚠️ have agents, not a planner

**Have:** per-source pinned agents (`PolicyAgent`/`WorkspaceAgent`/
`NotionAgent`/`DriveAgent`/`SlackAgent`/`LinearAgent`/`GitHubAgent`), routed
through a LangGraph `StateGraph` (`app/agent/orchestration.py`). Query
decomposition exists for compound questions (`app/rag/decompose.py`) —
sub-questions are retrieved independently and RRF-merged. Bounded retrieval
recovery (one alternate-phrasing retry) exists too.

**Missing:** routing is explicit user input (`agent: "policy"|"github"`) or
a deterministic Python function — never an LLM planner that decides *which*
tools/agents a complex query needs and in what order. There's no
cross-agent orchestration (e.g. "compare this GitHub commit against the
policy on code review" spanning two agents in one answer) — each agent
answers alone. No calculation/aggregation agent.

**Verdict:** deliberate, documented design choice (§2: "agent routing stays
deterministic — no LLM picks the agent... an aux-LLM intent classifier was
rejected"), not an oversight — but it does mean the video's "planner breaks
a complex request into steps across specialized agents" is explicitly not
what we do. If a real multi-agent-spanning-one-answer need appears, this is
the biggest architectural gap on the list.

## 6. Validation layer — ⚠️ have gates, not auditors

**Have:** two-layer anti-hallucination (confidence-gate cosine threshold +
strict grounded prompt with three response modes), prompt-injection
fencing + heuristic scrub on untrusted content, bounded recovery that
"never reduces grounding guarantees," GitHub agent's structural guarantee
(only tool output, fixed fallback on any failure).

**Missing:** no distinct post-generation validation *nodes* — no separate
"auditor" LLM call that re-checks a drafted answer against source chunks
before it reaches the user, no "strategist" that revises a plan mid-flight.
The gate/prompt IS the validation, but it's baked into generation, not a
separate verifying pass after a draft exists. Phase 20 (structural
`{claim, chunk_id}` citations + per-claim NLI) was scoped and explicitly
deferred pending a latency/cost decision — this is the closest match to the
video's "gatekeeper" concept and it's sitting in the backlog, not built.

**Verdict:** functionally we prevent most of what a validator would catch
(via the gate + prompt design), but there's no independent second-opinion
check on the LLM's own output — a hallucinated claim inside an
otherwise-grounded answer wouldn't be caught today. Phase 20 is the
directly-applicable next step, already designed, not started.

## 7. Evaluation metrics — ✅ have all three categories

**Have:** quantitative (golden-set path-firing, retrieval rank-of-correct-
chunk eval with no LLM — `evaluation/retrieval_eval.py`), qualitative
(RAGAS faithfulness/answer-relevancy/context-precision/recall, LLM-judged,
nightly), performance (`rag.query_signals` production logging: latency,
`top_score`, cache hits, retrieval-reused, response mode — plus CI-tracked
gating). Two-tier CI split by cost is itself a documented architectural
decision.

**Missing:** no dedicated cost-tracking dashboard (token usage is logged
per-call but not aggregated/alerted on), and the golden set is small (~17
cases) — acknowledged directly in CLAUDE.md as too small to trust for fine
threshold tuning.

**Verdict:** structurally this matches the video's three-category split
almost exactly. The gap is *scale* of the eval set, not *presence* of the
metric types.

## 8. Stress testing & red teaming — ⚠️ have injection defense, not a red-team practice

**Have:** prompt-injection hardening (delimiter fencing + heuristic scrub,
`app/security/untrusted.py`), a measured multi-run probe script
(`scripts/probe_injection.py --runs 15`) with honestly-reported leak rates,
golden injection test cases, cross-tenant isolation proofs
(`test_isolation.py`, `test_github_isolation.py`,
`test_github_workspace_scope.py`), rate limiting, and a whole section of
CLAUDE.md documenting live incidents found by deliberately hunting for
failure classes (memory OOM, unbounded fan-out, wrong gauges).

**Missing:** no systematic red-team suite covering bias, PII/information
leakage across the *conversation* layer (only cross-tenant leakage is
tested), no adversarial jailbreak corpus beyond the two golden injection
cases, no scheduled/repeated red-team run in CI (the probe script is
manual). CLAUDE.md is explicit that mitigations here are "partial, not
solved" and that a single golden PASS is not proof.

**Verdict:** we do more *ad hoc* adversarial hunting than most projects
(documented, measured, with honest negative results kept in the record),
but it's not a structured, repeatable red-team practice gating releases —
it's a probe script run by hand.

---

## Summary: what to prioritize if closing gaps

1. **Cheapest, highest-value:** hard metadata filters (date/department) —
   needs one schema column + one `WHERE` clause, no new subsystem.
2. **Already designed, just deferred:** Phase 20 structural citations + NLI
   validation layer — this is the direct answer to pointer 6.
3. **Cheap experiment:** hypothetical-question generation at ingest,
   piggybacking on the existing `contextualize.py` LLM call.
4. **Real architectural decision needed, not a bug:** whether to build an
   LLM planner/multi-agent-per-answer system (pointer 5) — current
   deterministic routing was a deliberate rejection of this, revisit only if
   a real cross-agent query need shows up.
5. **Process, not code:** turn `probe_injection.py` into a scheduled/CI red-
   team gate instead of a manual script.
