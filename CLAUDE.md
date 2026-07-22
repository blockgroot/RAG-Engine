# CLAUDE.md — project rulebook

> **Read this first.** It is the standing guide for every phase of this project.
> **Keep it current: update it at the end of each phase** (schema changes, new
> conventions, decisions, gotchas), not only when it is first written.

---

## 1. What this system is (and why it's built this way)

A **multi-tenant RAG platform for company policy Q&A**. Companies (tenants) upload
their policy documents; their employees ask questions and get answers grounded in
*their own* company's policies.

- **Why RAG, not fine-tuning.** Policies are *facts that change* (leave rules,
  reimbursement limits, etc.). Fine-tuning bakes facts into model weights and goes
  stale; RAG retrieves the current document text at question time, so updating a
  policy is just re-ingesting a file — no retraining, and answers can cite sources.
- **Multi-tenant.** Many companies share one deployment. Tenant data must be
  **strictly isolated** — one company must never see another's content.
- **Eventual goal.** A **self-hosted Docker image** an enterprise can run inside
  their own infrastructure (data never leaves their environment). This is why we
  favor components that can run locally with no external paid dependency.

## 2. Architectural decisions so far (and the reasoning)

- **Provider abstraction via a plain OpenAI-compatible client, not LiteLLM.**
  The `openai` client speaks a wire format most providers expose an endpoint for,
  so switching LLM provider is a config change (`LLM_MODEL` + `LLM_BASE_URL` +
  key), no code change. We chose this over LiteLLM to keep dependencies minimal.
  If we later need native-only features (e.g. Anthropic prompt caching), a
  LiteLLM-backed class can be dropped in *behind the same `LLMProvider` interface*
  without touching callers.
- **Local embeddings via sentence-transformers, not a paid hosted API.**
  Runs BGE-M3 in-process: $0, no API key, and document text never leaves the
  machine (critical for the self-hosted enterprise goal). A `remote` backend
  (OpenAI-compatible HTTP) exists as an alternative behind the same interface.
- **`org_id`-based multi-tenant isolation.** Every tenant-scoped table carries an
  `org_id`, and every read/write in the vector store *requires* one. Retrieval
  filters `WHERE org_id = ...` before ranking, so isolation does not depend on the
  vector index — it's enforced by the query itself. Proven by `tests/test_isolation.py`.
- **DB access goes through a psycopg connection pool.** `app/db/connection.py`
  owns a lazily-created module-level `ConnectionPool`; `register_vector` runs in
  its `configure` hook (once per pooled connection). Callers use
  `get_connection()` unchanged. Every process boundary must call `close_pool()`
  on exit (scripts' `main()` do this in `finally`; tests via a session fixture).
  Pooling ~halved query latency (p50 ~24ms → ~12ms) by amortizing connection
  setup. Sizing via `DB_POOL_MIN_SIZE` / `DB_POOL_MAX_SIZE`.
- **Everything is a swappable interface + factory.** Each capability (llm,
  embeddings, vectorstore) has: `base.py` (abstract contract), one or more
  concrete impls, a `factory.py` (`build_*` reads config and returns the impl),
  and an `__init__.py` exporting the public API. The rest of the app depends on
  the interface, never a concrete class.
- **RAG query path is an orchestrator, not a provider (`app/rag/`).** It composes
  the existing llm/embeddings/vectorstore interfaces (question + `org_id` →
  embed → retrieve → gate → grounded generate → `RagResult`). It deliberately has
  **no `base.py`**: there is only one way to orchestrate these pieces, so an
  abstract "RAG backend" contract would be ceremony with nothing to swap. It
  still keeps the `pipeline.py` + `factory.py` (`build_rag_pipeline`) + typed
  config shape for consistency. Providers are *injected* into `RagPipeline`, so it
  stays pure and testable and reuses the session-scoped provider fixtures.
- **Two independent layers keep answers grounded — not one.** (1) A **confidence
  gate**: if the top retrieved chunk doesn't clear `RAG_SIMILARITY_THRESHOLD`, we
  return the fixed fallback *without calling the LLM*. (2) A **strict prompt**
  (`app/rag/prompts.py`) that forbids outside knowledge and orders the model to
  emit the *same* fixed fallback when the context doesn't directly answer — even
  if it's on the same topic. Both were needed because a similarity threshold
  can't be *trusted* to separate "answerable" from "related-but-unanswered"
  questions (see §4 — the apparent separation is on far too small a sample to
  rely on). The gate stops noise cheaply; the prompt handles the fine-grained
  "on-topic but doesn't answer" judgement.
- **External sources are adapters behind one interface (`app/sources/`), using a
  thin official SDK — not a framework.** For Notion we use the official
  `notion-client` SDK (only dep: `httpx`), NOT `llama-index-readers-notion`. The
  reader pulls in all of `llama-index-core` (~30 transitive deps: nltk, sqlalchemy,
  tiktoken, …) and returns LlamaIndex `Document` objects we'd immediately unwrap —
  yet we already own preprocessing/chunking/embedding/storage and only need the
  raw-content-fetch piece. The SDK hands us plain API dicts, so we keep full
  control of block→text conversion and stay dependency-light. This is the *same*
  reasoning as the plain-OpenAI-client-over-LiteLLM call. Every source implements
  one `SourceAdapter` contract (`list_documents` / `fetch_document` /
  `get_last_modified`), so Google Drive/Docs/Sheets, GitHub, and Slack later
  implement the same interface — the ingestion pipeline never changes. Format
  conversion (Notion blocks → Markdown-ish text) lives *inside the adapter*, never
  in the ingestion pipeline.
- **Conversation memory rewrites before retrieval; it never changes the gate/prompt
  (`app/memory/` + pipeline).** A follow-up ("what about part-timers?") is
  meaningless to embed on its own, so with a `conversation_id` the pipeline first
  does a *cheap* `generate` call to rewrite it into a standalone question using the
  running summary + recent turns, then feeds that into the *unchanged* Phase 3
  retrieve→gate→generate path. History is Postgres-backed and org-scoped
  (`conversations` + `conversation_turns`). To bound prompt size/cost, the most
  recent `MEMORY_RECENT_TURNS` (=4) turns are kept verbatim and older turns are
  compressed into a running summary once the count exceeds `MEMORY_SUMMARIZE_AFTER`
  (=6); summarization is best-effort (skipped on LLM error). `RagResult` exposes
  `resolved_question` so the rewrite is observable/testable.
- **Web-search fallback uses real tool-calling, single-step, clearly labelled
  (`app/websearch/` + pipeline).** When internal retrieval fails the gate, the LLM
  is offered a `web_search` function (proper tool-calling — FreeLLMAPI supports it,
  verified) whose description tells it to call it ONLY for real, named, *external*
  entities (a specific insurer/product/company), and to NOT call it for internal
  company info (which stays the fixed fallback). If it calls the tool: exactly one
  bounded search runs, results are fed back, and the model composes one answer —
  no multi-step agent loop. The answer is prefixed with an unmistakable banner
  (`RagResult.source == "web"`) so a web answer never blends with a policy answer.
  Any failure/timeout/empty-result degrades to the fixed internal fallback.
  Default provider is **DuckDuckGo via `ddgs`** — keyless, free, matching §1's
  "no external paid dependency" principle (verified working); **Tavily** is the
  documented production-grade swap behind the same `WebSearchProvider` interface
  (LLM-native, 1000/mo free tier, no card). DDG is unofficial and rate-limits
  aggressively — fine for a fallback, not for bulk use.
- **Better retrieval = contextual chunks + hybrid search + reranking, under the
  UNCHANGED Phase 3 gate (`app/rag/retrieval.py`, `app/reranker/`, ingest).**
  Three techniques attack plain top-k's blind spots from different angles:
  (1) **Contextual retrieval** (ingest-time) — a short LLM-generated context is
  prepended to each chunk before embedding/storing, so a chunk carries its
  situating meaning into both the vector *and* the keyword index (Anthropic's
  idea; runs once per chunk at ingest, zero query latency). (2) **Hybrid search** —
  vector + Postgres full-text (BM25-style `ts_rank`) results fused with
  **Reciprocal Rank Fusion (k=60)**; RRF is rank-based so it needs no score
  normalization between cosine and ts_rank (totally different scales) — the
  settled default. This guarantees an exact term (a name, "part-time", a form
  code) is caught even when semantic similarity under-weights it. (3)
  **Cross-encoder reranking** — over-retrieve a `candidate_pool` (=30) then rerank
  with `BAAI/bge-reranker-v2-m3` (same family as BGE-M3, in-process via the
  `sentence-transformers` `CrossEncoder` we already depend on — no new dep, $0,
  local) and take the final `top_k`. Candidate pool 30 balances recall vs
  cross-encoder latency (~0.3s for 30 pairs after warmup). **The confidence gate
  is unchanged**: the retriever's `gate_score` is the best cosine similarity among
  candidates (== the vector top-1 the gate always used), so hybrid/reranking only
  change *which chunks in what order* reach the prompt, never the threshold logic.
  **MMR (Maximal Marginal Relevance) was considered and deliberately NOT
  implemented this phase.** Note: on a small corpus BGE-M3 already ranks the top
  chunk well, so these techniques are insurance for scale / exact-term recall /
  crowded ambiguous queries — the deterministic tests prove each mechanism.
- **The RAG logic is now a formal `PolicyAgent` behind a generic `Agent` interface
  (`app/agent/`).** Phase 7 extracted the retrieve→gate→generate→memory→web-fallback
  behavior out of the CLI scripts into one reusable unit. `Agent.answer(question,
  org_id, conversation_id) -> AgentResponse` is deliberately generic (answer,
  `grounded`, `source`, `citations`, `resolved_question`, `top_score`) — it says
  nothing about Notion/policies/retrieval, because a future GitHub agent will
  implement the *same* contract. Unlike `rag/`, this package *does* get a `base.py`:
  there genuinely is a second backend coming, so the abstraction earns its keep.
  `PolicyAgent` is a **thin adapter** over the unchanged `RagPipeline` (it maps
  `RagResult`→`AgentResponse`); the pipeline, gate, prompt, and every test outcome
  are byte-for-byte unchanged. `ask.py`/`chat.py` now call the agent, so the logic
  lives in exactly one place.
- **Golden-set evaluation is a two-tier regression gate, split by cost, not one
  monolith (`evaluation/` + CI).** ~17 hand-picked cases (10 answerable, 4 fallback,
  2 web, 1 conversation) against a deterministic corpus that *reproduces the real
  "Acme HR Policies" Notion data* — so it runs identically on a laptop and in CI
  (which can't reach Notion). The split follows the real cost asymmetry: **(1) fast
  tier, every push** — deterministic *path-firing* checks (did the right path fire;
  are known facts present; did the follow-up get rewritten), one LLM *generation*
  per case, plain asserts, no judge; this is the build gate. **(2) RAGAS tier,
  nightly + manual** — LLM-as-judge scoring (faithfulness / answer relevancy /
  context precision / recall), several judge calls per metric per case, gated
  against a baseline. RAGAS is wired to *our own* LLM endpoint + local BGE-M3
  embeddings (still $0 / self-hostable, §1) and is an **optional `[eval]` dependency**
  so the core runtime stays minimal. Chosen over a per-push RAGAS run because the
  judge cost buys little marginal signal at push cadence; chosen over no scoring at
  all because faithfulness is the direct anti-hallucination measure worth watching
  over time. Web cases are **advisory** (DuckDuckGo rate-limits; graceful
  degradation to the internal fallback is by-design), and answerable/conversation
  path checks **retry (3 attempts)** to absorb one-off LLM-generation variance on
  the free endpoint — a genuine regression fails every attempt. (Mitigation, not a
  guarantee: a hard gate should point at a deterministic model via secrets.)

## 3. Folder / file structure convention

```
app/
  config/       # typed settings (dataclasses w/ .from_env()). ONLY place that reads env.
  core/         # cross-cutting basics — the ProviderError exception hierarchy.
  llm/          # base.py (LLMProvider) + openai_provider.py + factory.py
  embeddings/   # base.py (EmbeddingProvider) + local.py + remote.py + factory.py
  db/           # Postgres plumbing: schema.sql, connection.py, migrate.py. Infra only.
  ingestion/    # preprocessing.py + chunking.py (text -> clean text -> chunks)
                #   + contextualize.py (P6: LLM context prefix per chunk at ingest)
                #   + pipeline.py (ingest_source: adapter -> chunk -> [context] -> embed -> store).
                #   Orchestrator like rag/ — composes existing interfaces; no base.py.
  vectorstore/  # base.py (VectorStore: query + keyword_search) + pgvector_store.py + factory.py
  rag/          # pipeline.py (RagPipeline/RagResult) + prompts.py + factory.py
                #   + retrieval.py (P6: HybridRetriever — vector+keyword RRF + rerank).
                #   Orchestrator, not a provider — composes the above; no base.py.
                #   Phase 5: also does query-rewrite (memory) + web-search fallback.
  reranker/     # base.py (Reranker) + local.py (CrossEncoder) + factory.py. P6
                #   cross-encoder reranking of the candidate pool (bge-reranker-v2-m3).
  sources/      # base.py (SourceAdapter) + notion.py + factory.py. External content
                #   sources (Notion now; Drive/GitHub/Slack later) behind one interface.
  memory/       # base.py (ConversationStore) + pg_store.py + factory.py. Org-scoped
                #   conversation history (turns + running summary) for follow-ups.
  websearch/    # base.py (WebSearchProvider) + duckduckgo.py + factory.py. The
                #   web-search tool used as the external-entity fallback.
  agent/        # base.py (Agent + AgentResponse + Citation) + policy_agent.py +
                #   factory.py. P7: the formal PolicyAgent (thin adapter over the RAG
                #   pipeline). HAS a base.py — a GitHub agent will implement it later.
evaluation/     # P7 golden-set eval (peer to scripts/tests). golden_set.py (cases +
                #   corpus mirroring real Notion data), harness.py (seed + run + path
                #   verdict), ragas_scoring.py (optional [eval] dep), report.py,
                #   run_eval.py (CLI). reports/ holds latest.md + GATE_FINDINGS.md (P7 Part 3).
scripts/        # entrypoints: verify_providers.py, init_db.py, demo_rag.py, ingest_notion.py,
                #   ask.py, chat.py (multi-turn), compare_retrieval.py (P6 before/after).
                #   ask.py/chat.py call the PolicyAgent (P7); logic lives only in app/agent.
tests/          # pytest; isolation (P2), grounding (P3), conversation+websearch (P5),
                #   retrieval (P6), golden-set path-firing (P7, test_golden_set.py)
.github/workflows/eval.yml  # P7 CI: fast path-firing tier every push + nightly RAGAS tier
```

**Conventions to follow (match, don't reinvent):**
- New capability = new package with `base.py` + impl(s) + `factory.py` + `__init__.py`.
  (Exception: an *orchestrator* that only composes existing interfaces — like
  `app/rag/` — skips `base.py`, since there's no second backend to abstract over.)
- All config lives in `app/config/settings.py` as a frozen dataclass with a
  `from_env()` classmethod. Nothing else calls `os.getenv` for config.
- All failures raise `ProviderError` (or a subclass in `app/core/exceptions.py`),
  carrying the original exception via `cause=` and `raise ... from`.
- `from __future__ import annotations` at the top; type hints everywhere; concise
  Google-style docstrings explaining *why*, not just *what*.
- The app depends on interfaces + `build_*()` factories, never concrete classes.

## 4. Known gotchas & past decisions worth remembering

- **We moved off paid embedding APIs.** DeepInfra (hosted BGE-M3) started
  returning `402 needs positive balance`. That triggered the switch to local
  sentence-transformers. Don't reintroduce a paid embedding dependency as the
  default.
- **Postgres runs via the bundled `docker-compose.yml`** (`pgvector/pgvector:pg17`,
  `docker compose up -d`), chosen for reproducibility and to match the eventual
  self-hosted image. The code only reads `DATABASE_URL`, so a managed instance or
  a local Homebrew `postgresql@17` + `pgvector` work equally well. (The dev
  otherwise prefers minimal containers — FreeLLMAPI on `localhost:3001` is the
  only other one.)
- **Embedding dimension is coupled to the schema.** `chunks.embedding` is
  `vector(1024)` because BGE-M3 outputs 1024 dims. If the embedding model changes,
  update BOTH `app/db/schema.sql` and `DatabaseSettings.embedding_dim` together,
  and re-create the table.
- **Migration must NOT use the pool.** The pool's `configure` hook runs
  `register_vector`, which requires the `vector` extension to exist. On a
  brand-new DB it doesn't yet, so `apply_schema` (in `migrate.py`) opens a
  *direct* `psycopg.connect` instead of `get_connection`. It only runs plain DDL
  and passes no vector params, so it needs neither the pool nor the adapters.
  Keep migration off the pool.
- **Isolation is enforced by the `WHERE org_id` clause, not the HNSW index.** Keep
  it that way — never expose a query path that omits `org_id`.
- **Preprocessing scope.** We assume text/Markdown input. Layout-aware extraction
  from PDF/DOCX/HTML (Unstructured/Docling) is deliberately deferred to a future
  ingestion-adapters phase.
- **Notion auth is an INTERNAL integration token this phase, not OAuth.** The
  adapter authenticates with `NOTION_TOKEN` (a Notion *Internal Integration
  Secret*, `ntn_...`). A *Public* integration's client id/secret are OAuth-only
  and CANNOT be used as an API token — that path needs a web app to catch the
  consent redirect, which is a later phase. `NOTION_CLIENT_ID/SECRET/REDIRECT_URI`
  are read into `NotionSettings` but unused for now (reserved, not hardcoded).
  The same `notion-client` accepts an OAuth token later via the same interface.
- **A Notion page must be explicitly shared with the integration** (page → `•••`
  → Connections → add it), separate from having a valid token. Without sharing,
  `list_documents()` returns zero pages even with a good token. `child_page`
  blocks are treated as separate documents (not inlined) since each Notion page
  is its own document.
- **Summarization threshold reasoning (Phase 5).** `MEMORY_RECENT_TURNS`=4 kept
  verbatim, summarize once total > `MEMORY_SUMMARIZE_AFTER`=6. Follow-ups almost
  always reference the last 1–2 turns, so 4 verbatim is ample headroom; triggering
  only above 6 leaves a 2-turn buffer so we're not summarizing every turn (each
  summary is an extra LLM call). The running summary preserves concrete facts a
  later turn might reference, so context survives pruning. Tune via env if needed.
- **Web search decides internal-vs-external via the tool description, not
  keywords.** The `web_search` tool is only offered when the gate fails; the model
  chooses to call it for real external named entities and declines (→ fixed
  fallback) for internal-company questions. This is a model judgement — keep the
  tool description explicit about the distinction; don't add keyword hacks.
- **Tool-calling is an optional LLM capability.** `LLMProvider.generate_with_tools`
  raises `NotImplementedError` by default; only `OpenAICompatProvider` implements
  it. It needs a backend model that supports function-calling (FreeLLMAPI does —
  verified; it routed to a tool-capable model and returned `tool_calls`).
- **The Phase 3 `rag` test fixture disables memory + web search** (passes
  `memory=None, web_search=None`) so grounding tests stay deterministic. Phase 5
  tests use dedicated fixtures (`rag_convo`, `rag_web`). `build_rag_pipeline` uses
  a sentinel so an explicit `None` means "capability off" vs omitted "build from
  config". Don't make the grounding fixture pick up web search, or its
  no-match/unanswered cases could wander off to a web query.
- **`RagResult.source` (`"policy"|"web"|"none"`) is how callers tell a
  policy-grounded answer from a web-sourced one from a refusal.** Web answers also
  carry the visible `WEB_ANSWER_LABEL` banner. `answered` is `True` for both
  policy and web answers; branch on `source` when the distinction matters.
- **Don't trust the apparent gap between "answerable" and "related-but-unanswered"
  scores — it's a tiny sample.** Measured with BGE-M3 against our own policy
  chunks, from only ~5 hand-picked questions (NOT an evaluation set):
  directly-answerable ≈ 0.54–0.74, topically-related-but-unanswered ≈ 0.46–0.48,
  unrelated noise ≈ 0.30. In this sample there's a gap between 0.48 and 0.54, but
  that is far too little data to rely on: with more questions an unanswered case
  could score above 0.48 or an answerable one below 0.54, closing it. So **a
  threshold cannot be trusted to tell "answers" from "on-topic but doesn't
  answer"** — that's why `RAG_SIMILARITY_THRESHOLD` is set low (0.35, just above
  noise) and the strict prompt does the fine discrimination. Don't raise the
  threshold to try to catch the "related-but-unanswered" case; you'll start
  rejecting real questions. A golden-set eval (see §6) is what would actually
  validate 0.35. Re-measure these bands if the embedding model changes.
- **The fixed fallback string lives in ONE place** (`RagSettings.fallback_response`
  / `RAG_FALLBACK_RESPONSE`). It is consumed in three spots that must agree: the
  confidence gate, the prompt's refusal instruction, and the pipeline's
  refusal-detection. Never hardcode a second copy — change it once.
- **`RagResult.answered` is the branch signal, not string-matching.** Both refusal
  paths (gate short-circuit and LLM refusal) set `answered=False` and normalise
  `answer` back to the exact fallback string, so callers check the bool, not the
  text. The LLM interface is single-prompt (`generate(prompt)`), so the "system"
  instructions are the top of that one prompt; if we later add a real system-role
  message, do it behind the same `LLMProvider` interface.
- **Hybrid/reranking only reorder — the gate still uses cosine top-1 (Phase 6).**
  `HybridRetriever` returns `gate_score` = best cosine among candidates (RRF/rerank
  never overwrite a chunk's cosine `.score`), so the confidence gate needs no
  recalibration. Don't feed RRF scores or reranker logits into the gate — the
  0.35 threshold is calibrated for cosine. `keyword_search` is an optional
  `VectorStore` capability (raises `NotImplementedError` by default; `PgVectorStore`
  implements it via a generated `content_tsv` + `websearch_to_tsquery`/`ts_rank`).
- **Contextual retrieval changes stored `content` (Phase 6).** With
  `INGEST_CONTEXTUAL_ENABLED` (default on), a chunk is stored as
  `"<LLM context>\n\n<original chunk>"`, so `RagResult.sources` / displayed chunks
  include the context prefix, and both the embedding and the keyword index benefit.
  It's best-effort (falls back to the raw chunk on LLM error) and adds one LLM call
  per chunk *at ingest only* — never at query time.
- **The reranker downloads ~2.2GB on first use** (`bge-reranker-v2-m3`), then is
  cached; inference is ~0.3s for 30 candidates after warmup. Swap to
  `bge-reranker-base` / `cross-encoder/ms-marco-MiniLM-L-6-v2` via `RERANKER_MODEL`
  if latency matters more than quality. Shared as one session fixture in tests.
- **On a small corpus BGE-M3 already ranks the top chunk well**, so a before/after
  rarely flips top-1 — the Phase 6 techniques are insurance for scale / guaranteed
  exact-term recall (keyword index) / crowded ambiguous queries. The deterministic
  `test_retrieval.py` cases prove each mechanism rather than relying on a dramatic
  small-corpus demo. **MMR was considered and deliberately not implemented.**
- **`PolicyAgent` must not add behavior — it's an adapter (Phase 7).** It maps
  `RagResult`→`AgentResponse` and nothing else; all retrieve/gate/generate logic
  stays in `RagPipeline`. If you're tempted to add answering logic to the agent,
  it belongs in the pipeline. The `Agent` interface is intentionally source-agnostic
  (no Notion/retrieval terms) so the future GitHub agent implements the same shape;
  don't leak policy specifics into `app/agent/base.py`.
- **The golden-set eval seeds a deterministic corpus, it does NOT read live Notion
  (Phase 7).** `evaluation/golden_set.py::CORPUS` reproduces the real Acme HR Notion
  facts inline so the eval runs anywhere (CI has no Notion token/DB access to the
  page). It ingests *plainly* (preprocess→chunk→embed, **no** contextual prefix) for
  determinism/speed — the hybrid+rerank+gate *retrieval* path is still fully
  exercised. If the real Notion page's facts change, update `CORPUS` to match.
- **RAGAS pins langchain to the 0.3 line (Phase 7).** `ragas 0.2.x` imports modules
  removed in langchain 1.x, so a plain `pip install ragas` breaks. The `[eval]`
  extra pins `langchain>=0.3,<0.4` (+ community/core/openai). RAGAS also downgrades
  `openai` to <2.0 — still `>=1.40`, so the app is unaffected. RAGAS is optional and
  never imported by the core runtime or the fast-tier path checks.
- **CI needs LLM secrets; the free dev endpoint is localhost-only (Phase 7).** Both
  eval tiers need an OpenAI-compatible LLM. `.github/workflows/eval.yml` reads it from
  repo secrets (`LLM_MODEL`/`LLM_API_KEY`/`LLM_BASE_URL`); with them absent (fork PRs)
  the LLM-gating step is skipped with a notice and only no-LLM structural checks run —
  so gating requires the secrets set. Postgres is a CI service; HF models are cached.
- **Answerable path checks retry once; web checks are advisory (Phase 7).** The free
  "auto" LLM is non-deterministic and can refuse a clearly-answerable question ~1 in
  5 (observed on `sick-leave-days`: correct chunk retrieved, gate cleared at 0.652,
  yet refused once — answered correctly 4/4 on retry; `health-plan` 0.672 behaved
  the same). So `harness.run_case_stable` retries `answerable`/`conversation` cases
  (`DEFAULT_ATTEMPTS`=3; a real regression fails every attempt — a mitigation, not a
  guarantee: point a hard gate at a deterministic model via secrets).
  `web` cases never gate CI (DuckDuckGo rate-limits; degrading to the internal
  fallback is by design) — only a web case answered as `policy` (fabrication) fails.
- **Part 3 gate evidence (Phase 7): the 0.35 gate is working as designed — do not
  raise it.** Across the golden set the gate produced **zero** false negatives
  (lowest answerable top_score 0.652, nearly 2× the threshold), and all four
  unanswerable cases cleared the gate (0.40–0.52) and were correctly refused by the
  strict prompt — the intended two-layer split. The apparent gap between the top
  unanswerable (0.523) and lowest answerable (0.652) is the tiny-sample trap of §4;
  raising the threshold into it would risk blocking real questions
  (`sick-leave-days` 0.652, `health-plan` 0.672 sit just above). Full write-up:
  `evaluation/reports/GATE_FINDINGS.md`. This is a *finding*, not a change — the gate
  is untouched.

## 5. Database tables (keep this in sync as the schema evolves)

Defined in `app/db/schema.sql`. Current tables:

| Table           | Responsibility                                                        |
| --------------- | -------------------------------------------------------------------- |
| `organizations` | Tenants. Everything else hangs off an org. Columns: `id`, `name`, `created_at`. |
| `documents`     | A source policy file/upload, scoped to one org. `id`, `org_id`, `title`, `source_uri`, `created_at`. As of Phase 4, `source_uri` is populated with the origin URL (e.g. the Notion page URL) at ingest. |
| `chunks`        | Text chunks + their `vector(1024)` embedding, scoped to one org. `id`, `org_id`, `document_id`, `chunk_index`, `content`, `embedding`, `created_at`. Phase 6: `content_tsv` (a `tsvector` GENERATED from `content`, GIN-indexed) powers keyword/hybrid search; `content` may include a prepended contextual-retrieval prefix. |
| `conversations` | (Phase 5) A conversation, scoped to one org. `id`, `org_id`, `summary` (running compression of pruned older turns), `created_at`. |
| `conversation_turns` | (Phase 5) One question+answer within a conversation. `id`, `conversation_id`, `org_id`, `turn_index`, `question`, `answer`, `created_at`. Older turns are pruned once summarized. |

Deletes cascade: removing an org removes its documents and chunks (and its
conversations + turns); removing a conversation removes its turns. Indexes:
`org_id` on documents and chunks (tenant filter) + an HNSW cosine index on
`chunks.embedding` (ranking speed).

**No `users`, `auth`, or OAuth tables yet** — deliberately deferred to a later phase.
**Phase 7 added no tables** — the PolicyAgent and golden-set eval are pure
application/tooling layers over the existing schema.

## 6. Current state: built vs. pending

**Built**
- Phase 1 — LLM & embedding provider abstraction (llm, embeddings, config, core).
- Phase 2 — DB schema (pgvector) behind a pooled connection layer, preprocessing
  + chunking, vector store abstraction. Tests: multi-tenant isolation +
  numpy→pgvector embedding round-trip, both passing.
- Phase 3 — RAG query path (`app/rag/`): question + `org_id` → embed → org-scoped
  retrieve → confidence gate → strict grounded prompt → LLM answer, returning a
  `RagResult` (answer, `answered` flag, `sources`, `top_score`). Two-layer
  anti-hallucination (gate + prompt). Tests: `test_grounding.py` proves an
  answerable question is grounded + traceable to one org's chunks, a no-match
  question falls back for both orgs, and a topically-related-but-unanswered
  question falls back via the prompt (top chunk cleared the gate). All passing.
- Phase 4 — First real external source: Notion (`app/sources/`). `SourceAdapter`
  interface (`list_documents` / `fetch_document` / `get_last_modified`) with a
  `NotionAdapter` (official `notion-client` SDK; block→text conversion inside the
  adapter). `app/ingestion/pipeline.py::ingest_source` wires adapter → preprocess
  → chunk → embed → store, scoped to a real org. Verified end-to-end against a
  real Notion page: an answerable question returns a grounded answer traceable to
  the page; an unanswered one falls back. Scripts: `ingest_notion.py`, `ask.py`.
- Phase 5 — Two independent additions to the RAG path. (A) Conversation memory
  (`app/memory/`): a `conversation_id` groups turns; a cheap LLM rewrite resolves
  follow-ups into standalone questions before retrieval; older turns compress into
  a running summary (recent 4 verbatim, summarize past 6). (B) Web-search fallback
  (`app/websearch/`): on gate failure the model may call a `web_search` tool (real
  function-calling) for real external named entities — single-step, bounded
  timeout, graceful degradation, answers labelled `source="web"`. Default provider
  DuckDuckGo (keyless); Tavily is the documented production swap. Tests
  (`test_conversation.py`, `test_websearch.py`): follow-up rewrite + retrieval,
  summarization + early-context resolution, external→web, internal→fallback,
  and a deterministic search-failure→fallback. Verified live against real Notion
  data. Scripts: `chat.py` (multi-turn), `ask.py` (shows `source`).
- Phase 6 — Better retrieval under the unchanged Phase 3 gate: (1) contextual
  retrieval at ingest (`app/ingestion/contextualize.py`), (2) hybrid vector +
  keyword search fused with RRF (`app/rag/retrieval.py`, `VectorStore.keyword_search`
  + `content_tsv`), (3) cross-encoder reranking of a 30-candidate pool
  (`app/reranker/`, `bge-reranker-v2-m3`). Wired into `RagPipeline` via an injected
  `HybridRetriever`; gate/generation and Phase 5 memory + web-search all unchanged
  underneath. MMR deliberately excluded. Tests (`test_retrieval.py`): exact-term
  via keyword, rerank promotes an out-of-cutoff chunk, multi-part coverage,
  contextual prefix; full suite (15) green. Script: `compare_retrieval.py`.
- `scripts/demo_rag.py` — a non-productionized END-TO-END demo (ingest → embed →
  store → retrieve → grounded LLM answer). Predates `app/rag/`; kept as a simple
  standalone walkthrough. The productionized path is now `app/rag/`.
- Phase 7 — (A) Formal **Policy Agent** (`app/agent/`): a generic `Agent` interface
  + `PolicyAgent` thin adapter over the unchanged `RagPipeline`; `ask.py`/`chat.py`
  refactored onto it. No behavior change — all 15 prior tests still green. (B)
  **Golden-set evaluation** (`evaluation/`): ~17 cases (answerable/fallback/web/
  conversation) over a deterministic corpus mirroring the real Notion data, scored
  two ways — deterministic path-firing (every push) + RAGAS faithfulness/answer-
  relevancy/context-precision/recall (nightly + manual). Wired into CI
  (`.github/workflows/eval.yml`) with a fast gating tier and a scheduled RAGAS tier;
  fails the build on a path regression or a below-baseline score. First baseline
  run: path-firing 15/15 gating (web advisory), RAGAS means faithfulness 1.00 /
  answer-relevancy 0.905 / context-precision 1.00 / context-recall 1.00. Tests:
  `test_golden_set.py` (path-firing, `-m "not network"`). (C) **Part 3 gate
  evidence** (`evaluation/reports/GATE_FINDINGS.md`): the 0.35 gate produced zero
  false negatives and correctly deferred all 4 unanswerable cases to the prompt —
  working as designed; recommendation is to NOT change it (a finding, not a change).

**Pending (not started)**
- Act on the Part 3 gate findings — a *decision*, not a default: the evidence says
  keep `0.35` and the two-layer design as-is (`evaluation/reports/GATE_FINDINGS.md`).
  Any future recalibration must be driven by an *expanded* golden set + production
  `top_score` logging, never the current ~17-case sample. Awaiting explicit sign-off.
- RAG enhancements: token-budget-aware context assembly and structured
  (machine-readable) citations. Current pipeline returns `sources` for
  traceability and asks the model to cite `[n]` inline, but does not yet parse
  citations out or trim context to a token budget.
- More source adapters, implementing the same `SourceAdapter` interface: Google
  Drive/Docs/Sheets, GitHub, Slack. (Notion done in Phase 4.)
- Incremental sync: use `SourceAdapter.get_last_modified` to re-ingest only
  changed documents instead of always re-adding (today each run creates a fresh
  org / re-adds documents; no dedup or update-in-place yet).
- Ingestion adapters: layout-aware extraction from PDF/DOCX/HTML.
- Users / auth / tenancy management, incl. full multi-tenant Notion OAuth
  (consent screen) once there's an app to host the redirect — client id/secret
  are already read into `NotionSettings`.
- API layer (HTTP endpoints) and an orchestrator.
- Packaging the self-hosted Docker image.

---

_When you finish a phase: update sections 4, 5, and 6 (and 2/3 if conventions
changed) before committing._
