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
  (`conversations` + `conversation_turns`). The most recent `MEMORY_RECENT_TURNS`
  turns are kept verbatim; older turns live in a running summary. **Phase 8 changed
  *how* that summary is maintained** (see the incremental-summarization bullet
  below) but not the rewrite path. `RagResult` exposes `resolved_question` so the
  rewrite is observable/testable.
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
  are byte-for-byte unchanged. The CLI (`scripts/cli.py`, Phase 9) calls the agent,
  so the logic lives in exactly one place.
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
- **Conversation summaries are now updated INCREMENTALLY, not in bulk (Phase 8,
  supersedes the Phase 5 threshold).** Before: history was kept verbatim until the
  turn count crossed `MEMORY_SUMMARIZE_AFTER`, then older turns were bulk-summarized
  at once. Now: after *every* turn, the single turn that just fell out of the
  verbatim window (`MEMORY_RECENT_TURNS`) is folded into the running summary via one
  LLM call whose input is *only* `existing summary + that one turn`
  (`RagPipeline._update_running_summary` + `build_summary_prompt`). So each update's
  cost is small and ~constant no matter how long the chat gets, and the summary is
  continuously current instead of lagging until a threshold trips. **Window size =
  3** (was 4): with incremental folding there's no threshold-lag to buffer against,
  so a smaller verbatim window is enough (follow-ups almost always reference the
  last 1–2 turns) and it keeps the rewrite prompt small. `MEMORY_SUMMARIZE_AFTER`
  was **removed** (no threshold exists any more). Storage is unchanged — it still
  uses `set_summary_and_prune` on the same `conversations.summary` +
  `conversation_turns` tables. Summarization stays best-effort (skipped on LLM
  error; the turn is folded in on the next turn instead).
- **Summary fold runs off the critical path (Phase 15, `app/rag/summary_fold.py`).**
  `_update_running_summary` is pure bookkeeping after the answer is already
  decided — nothing in `RagResult` depends on it — yet it used to run
  synchronously inside `answer()` before return, so every conversational turn
  past the verbatim window paid a full LLM round-trip before the caller (and
  any SSE/CLI stream) saw a character. It is now scheduled on a single-worker
  background executor; `answer()` returns immediately. **Barrier property
  (documented, not implicit):** `wait_for_conversation_fold(conversation_id)`
  waits on `_pending[conversation_id]` — that conversation's own outstanding
  fold Future — *not* on the shared executor's queue state in general and not
  on other conversations' folds. (Single-worker FIFO can still delay when that
  Future *runs* if another conversation's fold was queued earlier; that is
  scheduler ordering, not barrier keying.) Drain on API/CLI/test shutdown so a
  mid-exit drop is unlikely without a durable job queue. Measured live: turn-4
  answer returned in ~1.5s while the fold itself took ~1.7s in the background
  (before: user waited for both).
- **Indirect prompt injection hardened, not solved (Phase 16).** Retrieved
  chunks, contextualize inputs, recovery snippets, and web-search results are
  now treated as untrusted data: fenced with
  `<<<UNTRUSTED_DOCUMENT_CONTENT>>>`, explicit "data not instructions" rules
  (sandwich reminder on grounded/web), plus a narrow heuristic scrubber
  (`app/security/untrusted.py`) that strips common instruction-shaped spans
  (`***SYSTEM***` blocks, "ignore previous…", fake `</CONTEXT>` closers,
  `[ASSISTANT DIRECTIVE]`). Golden cases `injection-sabbatical` /
  `injection-dev-budget` + structural/output/scrub tests. **Honest limits:**
  not dual-LLM quarantine, not claim-level NLI, not robust to novel jailbreaks —
  a 15-run probe showed fencing alone still leaked ~60% on a strong SYSTEM
  payload; scrub zeroed *measured* leaks on that payload. Delimiting + scrub
  are partial mitigations.
- **Corpus-vocab query spelling before retrieval (Phase 17, `app/rag/query_normalize.py`).**
  First-time / standalone questions were embedded raw (only conversational
  follow-ups get an LLM rewrite; recovery spelling is reactive). An always-on
  LLM rewrite would add permanent latency/cost on every request — against the
  same "cheapest mechanism that works" instinct as retrieval-reuse. Choice:
  **SymSpell (`symspellpy`) against this org's chunk vocabulary**, plus a small
  common-query-English seed so clean words like "many" are not mapped onto rare
  corpus near-misses ("main"). Only the *retrieval key* is normalized;
  generation / web-decision still use the conversation-resolved question (same
  reason recovery preserves the original for generation). Defaults:
  `QUERY_NORM_ENABLED=true`, **max edit distance 1** (distance 2 falsely fixed
  external entities like Niva→five / Compare→company), min word length 4,
  Capitalized OOV tokens left alone. `VectorStore.list_chunk_texts(org_id)`
  feeds the dictionary. Kill-switch: `QUERY_NORM_ENABLED=false`. **Evidence
  honesty:** ARCHITECTURE.md's ~#18–24 "protien suppliments" finding was on
  live Notion Acme data; the measure harness uses golden+wellness (optionally
  `--noisy` distractors) and does *not* replay that corpus — on crowded synthetic
  pads the typo can stay rank #1 while cosine drops (~0.47→~0.74 after fix).
  Spelling + score recovery are proven; the exact mid-pool rank flip is not
  reproduced here.
- **Retrieval is reused across turns when the previous chunks still cover the
  follow-up — a cheap, deterministic, NON-LLM check before retrieval (Phase 8,
  `RagPipeline._try_reuse`).** On a follow-up, after the query-rewrite, the
  rewritten question is embedded (the same embedding retrieval would need anyway)
  and compared by **plain cosine** against the *previous turn's* retrieved chunks.
  If the best similarity clears `RETRIEVAL_REUSE_THRESHOLD`, those chunks are reused
  and retrieval (hybrid search + the cross-encoder rerank) is skipped; otherwise
  retrieval runs as normal. It is the *same kind of deterministic gate* as the
  Phase 3 confidence threshold — no LLM decides it. Crucially it **does not weaken
  the confidence gate**: the reuse similarity is a genuine cosine of *this* question
  vs the reused chunk, so it becomes `top_score` and the reused chunks flow through
  the **unchanged** gate → strict-prompt → generate path (if they don't actually
  clear the gate/answer the question, they're refused exactly as a fresh retrieval
  would be). The previous turn's chunks are persisted per-conversation
  (`conversation_last_retrieval`, one upserted row) storing chunk *text + locator
  only* — **not** embeddings, which are cheaply recomputed locally on the next turn
  (BGE-M3, ~ms for a handful of chunks; the tradeoff is a tiny recompute vs a DB
  vector column and it keeps the reuse decision deterministic). Reuse is org-scoped
  (never crosses a tenant boundary) and observable via `RagResult.retrieval_reused`.
  **Threshold = 0.72, deliberately conservative** — see §4 for the empirical
  reasoning and the finding that cosine cannot cleanly separate "same fact" from
  "adjacent topic" on this corpus.
- **One interactive CLI over the PolicyAgent, not a pile of scripts (`scripts/cli.py`,
  Phase 9).** The old one-shot `ask.py` + multi-turn `chat.py` were *retired* and
  replaced by a single interactive session: pick/pass an org, then a back-and-forth
  loop until `/exit`. It is a **thin shell** — it calls `PolicyAgent.answer` and only
  *formats* the result; it contains no retrieval/gate/generation logic (that stays in
  exactly one place, per Phase 7). Each turn surfaces the meaningful internals —
  query rewrite (`resolved_question`), retrieval reuse (`retrieval_reused`), answer
  provenance (`source`: policy/web/fallback, colour-coded), and grounding citations —
  so the behaviour built across Phases 3–8 is *visible* rather than a black box. The
  loop, per-turn rendering, and org resolution are split into pure functions
  (`converse` / `render_turn` / `resolve_org`) taking an injected agent + prompt
  callable, so the wiring is unit-testable offline without a real agent/DB/LLM.
  **`rich` is the chosen library**: a clean, glanceable terminal UI (panels,
  colour-coded provenance, aligned citations, width-aware wrapping) would otherwise
  be a lot of hand-rolled ANSI. It's a small, pure-Python, **presentation-only**
  dependency confined to `scripts/cli.py` — nothing under `app/` imports it, so the
  core runtime and the eventual self-hosted image stay dependency-light (§1). A tiny
  optional `VectorStore.list_organizations` was added (default `NotImplementedError`,
  like `keyword_search`) purely to power a friendly org picker.
- **Bounded retrieval recovery for Retrieval Discovery Gaps (extends Phase 3 path).**
  The first retrieve runs exactly as today. Only when available evidence looks
  insufficient — gate miss, or the generation stage finds the context insufficient
  (currently implemented via ``_is_refusal``, not defined as such architecturally) —
  may **at most one** recovery attempt run: an LLM produces alternative
  retrieval-oriented search expressions (synonyms, abbreviations, spelling
  corrections, document terminology, alternate phrasings, related vocabulary)
  while **preserving user intent**, those are retrieved and RRF-fused with
  first-pass hits, then the **unchanged** gate + grounded prompt apply again.
  Recovery never answers the question and must never reduce grounding guarantees.
  Expander failure degrades to the existing path. Internal recovery runs *before* web search on gate miss. Web is also
  offered when generation finds evidence insufficient after a cleared gate
  (weak neighbors) — the model still declines for internal-only questions. Config: ``RecoverySettings`` / ``RECOVERY_ENABLED`` /
  ``RECOVERY_MAX_QUERIES``. Observability on ``RagResult``:
  ``recovery_used``, ``recovery_reason`` (``gate_miss`` | ``insufficient_evidence``),
  ``recovery_queries``, ``retrieval_improved``, ``top_score_before`` /
  ``top_score_after``, ``final_answer_source``, ``latency_ms``.
- **Grounding prompt has three response modes (Grounding Gap).** Explicitly
  Supported / Related but Not Explicit / No Supporting Evidence. Related mode
  may report what documents say while distinguishing that they do not explicitly
  answer — unsupported conclusions remain forbidden.
- **Per-organization Notion credentials: one integration secret PER org, discovered
  from config, never a shared token (Phase 9).** Going forward each real org gets its
  OWN Notion internal integration + secret, expressed as a distinctly-named env var
  `NOTION_TOKEN_<NAME>`. `NotionSettings.from_env` discovers *all* of them generically
  (scan env for the prefix → `{name: secret}` map), so **nothing hardcodes how many
  orgs exist or their names** — adding an org later is one new env var + one ingestion
  run (`ingest_notion.py --org "Name" --token <name>`), never a code change.
  `NotionSettings.resolve_token(name)` returns *only* that org's secret and raises if
  it's missing — it will **never silently fall back** to another org's token or the
  global `NOTION_TOKEN` (that default is used only when a run names no token, e.g. the
  single Phase 4 test org). Why this shape: because a Notion integration can only see
  pages explicitly shared with it, a per-org secret makes the tenant boundary a *real,
  external* access boundary enforced by Notion itself — not merely something our code
  keeps straight — matching how isolation is enforced everywhere else here, and a
  faithful stand-in for the per-customer OAuth that replaces it later (same
  `SourceAdapter`, only how the token is obtained changes). **Deferred on purpose:**
  no frontend, HTTP API, OAuth flow, or admin/user-role handling this phase — that is
  the *next* stage. Real multi-org data entry + ingestion also happens later; Phase 9
  only lays the credential plumbing.
- **Phases 10-14 build the product layer this was always heading toward: real
  per-org OAuth (replacing the env-var token), an admin panel, a durable ingestion
  job queue, an HTTP API, streaming chat, and a frontend portal — all wired
  *around* the RAG engine, never replacing its gate/prompt/isolation logic.**
  - **Identity is new, additive, and never bypasses `org_id` scoping (Phase 10).**
    Four new tables (`users`, `org_domains`, `oauth_connections`, `ingestion_jobs`)
    follow the exact existing schema conventions (UUID PK, `org_id` FK +
    `idx_<table>_org`, idempotent `IF NOT EXISTS`). `oauth_connections` has
    `UNIQUE (org_id, provider)` so a lookup can never be ambiguous across tenants.
    `app/security/crypto.py` encrypts OAuth tokens at rest with `MultiFernet`
    (`AUTH_ENCRYPTION_KEYS`, comma-separated, first key encrypts/all keys tried on
    decrypt) — key rotation with **no external KMS dependency**, keeping the
    self-hosted principle in §1.
  - **OAuth is a new `app/auth/` provider interface, the same shape as every other
    capability (Phase 11).** `OAuthProvider` (`authorize_url`/`exchange_code`/
    optional `refresh`) + `NotionOAuthProvider` (Notion's public OAuth2 endpoints,
    reusing the `NotionSettings.client_id/secret/redirect_uri` scaffolded-but-unused
    since Phase 4) + `factory.py`. Credentials move from env vars to
    `oauth_connections` (`app/auth/credentials.py`), but the **legacy
    `NOTION_TOKEN_<NAME>` env path is kept fully independent, with NO fallback
    between the two** — a design review flagged that a shared/ambiguous credential
    source is exactly how a cross-org leak would happen, so the two paths never
    touch. `build_source_adapter` gained an optional `token` param (an
    already-resolved secret) alongside the existing `token_name` env lookup.
  - **Ingestion is now a durable, admin-triggered Postgres-backed queue, not a
    blocking script (Phase 12).** `app/jobs/queue.py`: `claim_next()` atomically
    claims the oldest queued job via `UPDATE ... WHERE id = (SELECT ... FOR UPDATE
    SKIP LOCKED)` — safe for concurrent workers, no double-claim. `reap_stuck()`
    flips a job stuck `running` past a timeout back to `failed`, so a crashed
    worker doesn't leave it running forever. `app/jobs/worker.py` runs the
    **existing, unchanged** `ingest_source()` pipeline; failures are caught and
    recorded on the job, never left to crash the worker. Deliberately **no new
    infra** (no Redis/Celery) — reuses the Postgres pool already everywhere else.
    `scripts/run_worker.py` is its own long-lived process, separate from the API,
    so a crashed worker never takes the API down and vice versa.
  - **Chat "streaming" chunks an already-fully-decided answer; it does not stream
    raw LLM tokens (Phase 13a).** Tracing `RagPipeline._run()`'s control flow showed
    a gate-passing `_generate()` call is not necessarily final — evidence-
    insufficiency can still trigger a recovery-and-regenerate, and a
    recovery-exhausted miss can still fall through to the web-search tool (a
    different call shape, `generate_with_tools`). Streaming tokens from a call that
    might get discarded and replaced would leak a draft or require buffering
    anyway — no correctness win, only risk to grounding. So
    `RagPipeline.answer_stream()` / `PolicyAgent.answer_stream()` run the complete
    **unchanged** `answer()` (every gate/recovery/grounding decision resolved
    exactly as always) and only then chunk the final text for progressive
    delivery. This is a deliberate deviation from an earlier sketch that proposed
    a raw `generate_stream()` passthrough — discovered during implementation, not
    a design assumed up front.
  - **The HTTP API (`app/api/`, FastAPI) is the ONLY place `org_id` enters a
    request, always from the signed session, never from client input (Phase
    13b-d).** `deps.get_session`/`require_admin` decode the session JWT
    (`app/auth/session.py`) from an httpOnly cookie; every router downstream takes
    `org_id`/`role` from there exclusively. **Login is magic-link only**
    (`app/api/auth.py`): originally an email got a link only when its domain was
    DNS-verified AND had auto-join explicitly enabled by an admin; **this domain
    auto-join mechanism was removed in a later simplification pass — see the
    bullet below — and replaced by direct admin-invited members.** The response
    to a magic-link request is **always the same generic message**, whether or
    not the email is known — this endpoint must never be usable to enumerate
    registered accounts. Magic-link
    tokens and OAuth `state` values are single-use and server-side (only a
    SHA-256 hash of a magic-link token is ever stored), consumed atomically on
    lookup so a captured link/URL can't be replayed. **A session is never issued
    for a user with no resolved `org_id`** — there is no authenticated state that
    lacks a tenant. Admin OAuth connect (`/auth/{provider}/authorize` +
    `/callback`) is fully independent of magic-link auth and only ever produces an
    `oauth_connections` row. A client-supplied `conversation_id` on
    `POST /chat/stream` is explicitly checked against the caller's `org_id` before
    ever reaching the agent — `ConversationStore.get_context`/`append_turn` take
    only a `conversation_id` with no org check by design (fine for the CLI's
    trusted internal calls; not fine once exposed to arbitrary HTTP clients), so
    the router is the one place that closes that gap.
  - **The frontend (`frontend/`, Next.js 15 App Router) is a separate app calling
    the API with `credentials: "include"` — the session lives only in the httpOnly
    cookie, never JS-accessible storage (Phase 14).** No Tailwind/UI-kit
    dependency; plain CSS variables implement a "Technical Editorial" design
    (serif display + monospace data pairing, system font stacks — no network font
    fetch). Citations render as first-class bordered source cards with a
    color-coded provenance stripe (policy/web/fallback), mirroring the existing
    CLI's `_SOURCE_STYLE`. `ConnectionCard` is provider-agnostic from day one
    (Google/GitHub use the *same* component as Notion — Google is now a live
    connectable source; GitHub still renders "coming soon") — mirrors the
    backend factory's extension pattern. SSE streaming
    uses `fetch` + `ReadableStream` (not `EventSource`, which can't POST a body).
- **Google Drive/Docs is a second `SourceAdapter` + `OAuthProvider` (Google
  Integration Plan), coexisting with Notion under provider-partitioned sync.**
  Sync state is keyed on `(org_id, source_provider, source_external_id)` so a
  Google sync never deletes Notion docs (and vice versa). OAuth-only (no
  env-var Google token path). Native Google Docs only (Markdown via
  `files.export`); admin pastes one Drive folder URL stored as JSONB
  `oauth_connections.source_config`. Token refresh lives in
  `get_live_connection_token` (provider-agnostic). Drive calls use plain
  `httpx` (no `google-api-python-client`). Deployment model: **internal-use
  OAuth client** (exempt from Google verification / 7-day refresh expiry).
  Gate/prompt/retrieval unchanged — Google chunks are ordinary org-scoped rows.
- **Domain-based auto-join was removed in favor of direct admin-invited
  members — deferred, not wrong.** Phase 13 originally gated employee login on
  a per-org `org_domains` claim (an admin typed a domain, toggled
  `auto_join_enabled`) resolved via `resolve_org_for_email`. That machinery was
  built correctly — it's the right shape *for onboarding many self-serve
  companies at once* — but it's solving a problem this deployment doesn't have
  yet: a small number of known users, not open self-serve signup across many
  tenants. Carrying `org_domains`, the eligibility-resolution path, and the
  admin domain-management UI was more complexity than the current need
  justified, so it was **removed** (table dropped via `DROP TABLE IF EXISTS
  org_domains` in `schema.sql`, `app/auth/domains.py` deleted) rather than left
  half-wired. **What replaced it:** `POST /admin/members` — an admin names a
  specific email directly (`app.auth.users.invite_member`), which creates a
  `users` row scoped to their own `org_id` with no domain matching of any
  kind. `request_magic_link` now only ever sends a link to an email that
  *already has an account* (created at signup, or via an admin invite) —
  there is no path left that creates a first account from an unrecognized
  email. The signup flow (a brand-new org's first admin) was untouched by
  this change — it was later gated behind manual approval, see the
  signup-approval-queue entry below. **To revive self-serve domain
  auto-join later** (e.g. once onboarding many companies without manual
  admin invites is an actual need): restore the `org_domains` table and
  `app/auth/domains.py` from git history (the commit that removed them),
  and re-wire `resolve_org_for_email` back into `request_magic_link`
  alongside the invite path (the two aren't mutually exclusive — an admin
  invite and a domain claim could both resolve an org for an email). Don't
  rebuild it from scratch; the DNS-verification design was already
  reasoned through once.
- **Self-serve org creation is gated behind manual platform-owner approval —
  signup no longer creates an org or admin synchronously.** Until now,
  `POST /auth/signup` immediately called `store.create_organization(...)` +
  `create_admin(...)`: anyone could show up, name any company name, and
  become that org's admin with zero verification — a real gap for a
  multi-tenant platform where "this org's admin" is a trust boundary.
  `signup()` (`app/api/auth.py`) now only inserts a `pending` row into a new
  `org_signup_requests` table (`app/auth/signup_requests.py`:
  `create_signup_request`/`get_pending_request_for_email`/
  `list_signup_requests`/`approve_signup_request`/`reject_signup_request`) —
  no org, no user, no magic-link email, since there's no account yet to sign
  into. The platform owner reviews the queue via
  `scripts/review_signup_requests.py list/approve/reject`, **deliberately a
  CLI and not a new HTTP/session/cookie surface** — this is a single-operator,
  self-hosted deployment, and a second login system would be new attack
  surface bought for a capability only the deployer ever needs. Approving
  reuses the exact same `store.create_organization` + `create_admin` calls
  signup used to make directly, then emails the requester a magic-link
  sign-in via a new `send_signup_approved_email` (from here on the *existing*
  invited-member/magic-link login path is used unchanged — this only gates
  how the *first* admin account for a *new* org comes into being).
  Rejecting records an optional reason and emails `send_signup_rejected_email`.
  A partial unique index (`idx_org_signup_requests_email_pending`, same
  pattern as `idx_oauth_connections_org_provider_orgwide`) blocks a second
  signup while one is already pending, but re-submitting after a rejection is
  allowed (a rejected row no longer matches the partial index).
- **Session TTL defaults to 30 days, not a typical short web session** (`AUTH_SESSION_TTL_MINUTES`,
  `app/auth/session.py` + the `max_age` on the session cookie in `app/api/auth.py`) —
  deliberate given this is a low-risk internal tool with an already-hardened cookie
  (httpOnly+Secure+SameSite=Lax) and no refresh-token flow; revisit with a proper
  refresh mechanism if the risk profile changes.
- **Workspace-within-a-Workspace: a `workspace_id` isolation axis nests INSIDE
  `org_id`, everywhere `org_id` already scopes a row — it never replaces it.**
  An employee can create a personal sub-workspace inside their own org, invite
  a few org colleagues into it, connect their own Notion/Drive source (e.g.
  meeting notes), and have questions asked *in that workspace* answered ONLY
  from its own content. Every table that already carried `org_id` for tenant
  scoping (`documents`, `chunks`, `conversations`, `conversation_turns`,
  `oauth_connections`, `ingestion_jobs`, `oauth_states`) got one new nullable
  `workspace_id` column: `NULL` = today's org-wide row, completely unchanged
  behavior; non-`NULL` = scoped to that sub-workspace. Every read/write pairs
  `workspace_id` with `org_id` — **never `workspace_id` alone** — matching the
  same "ambiguity must be structurally impossible" discipline `oauth_connections`
  already used for `(org_id, provider)`. A workspace query sees ONLY its own
  workspace's rows, never also the org-wide ones (`WHERE org_id = :org AND
  workspace_id = :workspace`, no `OR workspace_id IS NULL`) — deliberate: a
  meeting-notes workspace silently blending in HR policy chunks would make
  workspace membership meaningless as an access boundary. `app/workspaces/`
  is a new, separate boundary from the org: an invited workspace member must
  already be a `users` row in the SAME `org_id` (enforced by
  `invite_member`/`assert_member`) — a sub-workspace can never admit someone
  from a different tenant; the org boundary (Notion-integration-enforced, per
  the bullet below) stays the outermost wall, the workspace is an inner,
  app-enforced one. The gate/strict-prompt/reranker/memory pipeline
  (`RagPipeline`, `PolicyAgent`) is reused byte-for-byte — `answer()` /
  `answer_stream()` just gained an optional `workspace_id` threaded to every
  retrieval call site (`_retrieve_once`, `_retrieve_for_subquestions`,
  `_recover_once`, `HybridRetriever.retrieve`, `VectorStore.query`/
  `keyword_search`), the query-answer cache key, and `ConversationStore.
  create_conversation` — never a second gate/prompt implementation.
  `oauth_connections` uniqueness is `(org_id, provider)` for the org-wide
  connection and `(org_id, provider, workspace_id)` for a personal one, via
  TWO PARTIAL unique indexes (`WHERE workspace_id IS NULL` /
  `WHERE workspace_id IS NOT NULL`) rather than one plain
  `UNIQUE(org_id, provider, workspace_id)` — Postgres treats `NULL` as
  distinct-from-`NULL` in a multi-column `UNIQUE`, which would let unlimited
  org-wide rows through. `save_connection` picks the matching `ON CONFLICT`
  target by whether `workspace_id` is `None`, since Postgres requires the
  inference clause's predicate to match the target partial index's predicate
  exactly. Only a workspace's `owner` (its creator) may invite members or
  connect/reconnect its source (`GET /auth/{provider}/authorize?workspace_id=`,
  `POST /workspaces/{id}/members`) — an ordinary member can only ask
  questions, so they can't silently repoint the workspace's data source.
  **Gotcha hit while building this:** an early schema edit added
  `workspace_id` to `ingestion_jobs` via `ALTER TABLE` placed BEFORE that
  table's own `CREATE TABLE` in `schema.sql` — invisible on an already-
  migrated dev DB, but broke `apply_schema` from scratch on a fresh CI
  database (`relation "ingestion_jobs" does not exist`). Every `ALTER TABLE
  ... ADD COLUMN` for a new cross-table column MUST come after that table's
  `CREATE TABLE`, and should be verified by applying `schema.sql` against a
  genuinely fresh throwaway database, not just re-applying to an already-
  migrated one (idempotency alone doesn't catch ordering bugs).

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
                #   + retrieval.py (P6: HybridRetriever — vector+keyword RRF + rerank)
                #   + query_normalize.py (P17: corpus-vocab SymSpell before retrieve)
                #   + summary_fold.py (P15: deferred running-summary fold).
                #   Orchestrator, not a provider — composes the above; no base.py.
                #   Phase 5: also does query-rewrite (memory) + web-search fallback.
                #   Phase 8: incremental summary update + pre-retrieval reuse check.
  reranker/     # base.py (Reranker) + local.py (CrossEncoder) + factory.py. P6
                #   cross-encoder reranking of the candidate pool (bge-reranker-v2-m3).
  sources/      # base.py (SourceAdapter) + notion.py + google_drive.py +
                #   google_drive_utils.py (folder URL parse + files.get validate) +
                #   factory.py. External content sources (Notion + Google Drive;
                #   GitHub/Slack later) behind one interface.
  memory/       # base.py (ConversationStore) + pg_store.py + factory.py. Org-scoped
                #   conversation history (turns + running summary) for follow-ups.
                #   P8: incremental summary update + set_last_retrieval/get_last_retrieval
                #   (last turn's chunks, for the retrieval-reuse check).
  websearch/    # base.py (WebSearchProvider) + duckduckgo.py + factory.py. The
                #   web-search tool used as the external-entity fallback.
  agent/        # base.py (Agent + AgentResponse + Citation) + policy_agent.py +
                #   factory.py. P7: the formal PolicyAgent (thin adapter over the RAG
                #   pipeline). HAS a base.py — a GitHub agent will implement it later.
                #   P13: policy_agent.py also has answer_stream() (chunks the
                #   already-decided answer; not on the abstract Agent base).
  security/     # P10: crypto.py (encrypt/decrypt via MultiFernet) for OAuth tokens
                #   at rest. A tiny utility module, not an interface+factory package
                #   (only one real capability, no second backend to abstract over).
  auth/         # P10-13 + Google: identity + OAuth "Connect X" + sessions. base.py
                #   (OAuthProvider) + notion_oauth.py + google_oauth.py + factory.py
                #   + credentials.py (oauth_connections + live token refresh +
                #   source_config) + users.py + magic_link.py + oauth_state.py +
                #   session.py + email.py. domains.py was REMOVED — see §2.
  jobs/         # P12: Postgres-backed durable ingestion job queue. queue.py
                #   (enqueue/claim_next/reap_stuck/get_job, SELECT ... FOR UPDATE
                #   SKIP LOCKED) + worker.py (runs the unchanged ingest_source()).
                #   No base.py — one queue implementation, no second backend.
  workspaces/   # Workspace-within-a-Workspace: sub-workspace CRUD + membership.
                #   store.py (create_workspace/invite_member/assert_member/
                #   list_my_workspaces/list_workspace_members). No base.py — one
                #   storage backend, like app/jobs/. assert_member is the ONE
                #   place a workspace_id is validated against a user's org_id
                #   before any downstream code trusts it (mirrors deps.get_session
                #   for org_id).
  api/          # P13: the HTTP layer (FastAPI). main.py (app + CORS) + deps.py
                #   (get_session/require_admin — the ONLY place org_id enters a
                #   request; get_workspace_role/require_workspace_owner — the
                #   same for workspace_id) + auth.py (magic-link + OAuth connect
                #   routes, workspace-scoped connect via ?workspace_id=) +
                #   admin.py (members/connections/jobs, all org-scoped from the
                #   session) + chat.py (SSE streaming, optional workspace_id) +
                #   workspaces.py (create/invite/connections/jobs for a
                #   sub-workspace) + orgs.py (/me).
evaluation/     # P7 golden-set eval (peer to scripts/tests). golden_set.py (cases +
                #   corpus mirroring real Notion data), harness.py (seed + run + path
                #   verdict), ragas_scoring.py (optional [eval] dep), report.py,
                #   run_eval.py (CLI). reports/ holds latest.md + GATE_FINDINGS.md (P7 Part 3).
scripts/        # entrypoints: verify_providers.py, init_db.py, demo_rag.py, ingest_notion.py
                #   (P9: --org/--token per-org ingestion), cli.py (P9: the single
                #   interactive chat), compare_retrieval.py + demo_phase8.py (before/after
                #   demos), run_worker.py (P12: long-lived ingestion job worker process).
                #   cli.py calls the PolicyAgent (P7); logic lives only in app/agent.
                #   (P9 retired ask.py + chat.py — cli.py replaces both.)
frontend/       # P14: Next.js 15 App Router portal, separate app calling app/api/
                #   over HTTP with credentials: "include" (session cookie only,
                #   never JS-accessible storage). (auth)/login + (auth)/verify
                #   (magic-link), chat/ (streaming SSE + citations, accepts
                #   ?workspace=<id> — same component parameterized, not a
                #   forked chat UI), admin/members|connections|jobs, workspaces/
                #   + workspaces/[id] (Workspace-within-a-Workspace: create,
                #   invite, connect a personal source, workspace-scoped chat
                #   link). No Tailwind/UI-kit — plain CSS vars.
tests/          # pytest; isolation (P2, extended with workspace-vs-org-wide and
                #   workspace-vs-sibling-workspace leak proofs), grounding (P3),
                #   conversation+websearch (P5), retrieval (P6), golden-set
                #   path-firing (P7, test_golden_set.py), security/auth/jobs/
                #   streaming/api_* (P10-13, test_security.py, test_auth.py,
                #   test_identity.py, test_jobs.py, test_streaming.py,
                #   test_api_auth.py, test_api_admin.py, test_api_chat.py),
                #   test_workspaces.py + test_workspace_rag.py + test_api_workspaces.py
                #   (Workspace-within-a-Workspace: membership, RAG scoping, API).
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

- **Not every schema.sql addition has the ALTER-ordering hazard.**
  `org_signup_requests` (signup-approval queue, §2/§5) is a plain
  `CREATE TABLE IF NOT EXISTS` with no `ALTER TABLE ... ADD COLUMN` on an
  existing table, so it carries none of the "must come after the table's own
  CREATE TABLE" hazard documented below for `ingestion_jobs`/`workspace_id`.
  Worth saying explicitly since a reader who's just seen that gotcha might
  assume every new table/column addition needs the same care — only `ALTER
  TABLE` on a table defined elsewhere in the file does.
  `idx_org_signup_requests_email_pending` also reuses the partial-unique-index
  pattern from `oauth_connections` (`idx_oauth_connections_org_provider_orgwide`)
  — a plain multi-column `UNIQUE` can't express "one row per email while
  `status='pending'`" because dropping the `WHERE` clause would also block
  re-submitting after a rejection.
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
  adapter authenticates with a Notion *Internal Integration Secret* (`ntn_...`). A
  *Public* integration's client id/secret are OAuth-only and CANNOT be used as an
  API token — that path needs a web app to catch the consent redirect, which is a
  later phase. `NOTION_CLIENT_ID/SECRET/REDIRECT_URI` are read into `NotionSettings`
  but unused for now (reserved, not hardcoded). The same `notion-client` accepts an
  OAuth token later via the same interface.
- **Recovery is bounded and optional; expander failure never fails the request.**
  At most one expand per ``answer()``. Happy path (gate pass + sufficient generation)
  adds zero recovery LLM calls. On expander timeout/parse failure, continue with
  the existing gate/web/fallback path. Kill-switch: ``RECOVERY_ENABLED=false``.
  Recovery queries are retrieval-only — generation always uses the original
  (conversation-resolved) question. Do not introduce domain-specific synonym rules.
- **Notion tokens are per-org and must NOT fall back (Phase 9).** Each org has its
  own `NOTION_TOKEN_<NAME>` secret; `NotionSettings.resolve_token(name)` returns
  *only* that org's token and raises `ConfigurationError` if it's missing — it must
  never silently substitute another org's token or the global `NOTION_TOKEN`, or the
  Notion-enforced tenant boundary would leak. The bare `NOTION_TOKEN` is the default
  used *only* when a run names no token (the Phase 4 test org). Discovery is
  generic (scan env for the `NOTION_TOKEN_` prefix) — don't hardcode org names/count
  anywhere. `build_source_adapter("notion", token_name=...)` is how a run selects one.
- **Provider-partitioned sync is mandatory for multi-source orgs.** Every
  `list_source_documents` / upsert / delete / `detect_source_changes` /
  `ingest_source` path takes an explicit `provider`. Without it, the first Google
  sync would treat every Notion page id as "removed" and cascade-delete those
  chunks. Proven by the coexistence cases in `tests/test_incremental_sync.py`.
- **Google Drive returns 404 (not 403) for files the token can't see** — treated
  as inaccessible/removed. Folder config is validated via `files.get` before
  save. Prefer an **internal-use** Google OAuth client in Workspace (exempt from
  verification and the testing-mode 7-day refresh expiry); testing-mode clients
  will silently break weekly when refresh tokens die (`invalid_grant` →
  `OAuthReauthRequiredError`, actionable "reconnect" — never retry-loop).
- **Google has no env-var token path** — only `oauth_connections` +
  `get_live_connection_token`. A Drive connection also requires
  `source_config.folder_id` before sync/changes (admin pastes a folder URL).
- **A Notion page must be explicitly shared with the integration** (page → `•••`
  → Connections → add it), separate from having a valid token. Without sharing,
  `list_documents()` returns zero pages even with a good token. `child_page`
  blocks are treated as separate documents (not inlined) since each Notion page
  is its own document.
- **Summarization is incremental as of Phase 8 (this supersedes the Phase 5
  threshold reasoning).** `MEMORY_RECENT_TURNS`=3 kept verbatim; every turn that
  falls out of that window is folded into the running summary immediately, one turn
  per update. There is **no** `MEMORY_SUMMARIZE_AFTER` any more — it was removed.
  Window went 4→3 because incremental folding removed the threshold-lag a larger
  buffer used to hide (older turns are now *always* current in the summary, not
  stale until a trigger). Each update is one LLM call over `summary + one turn`, so
  cost is ~constant regardless of length (vs the old bulk call that grew with the
  batch). Best-effort still holds: on an LLM error the fold is skipped and retried
  on the next turn (a small bounded backlog, never the full history). The running
  summary preserves concrete facts a later turn might reference. Tune the window via
  `MEMORY_RECENT_TURNS`.
- **Phase 15 summary-fold barrier is per-conversation, not global.**
  `wait_for_conversation_fold` looks up `_pending[conversation_id]` and waits on
  that Future only (`app/rag/summary_fold.py`). Do not "simplify" it into a
  process-wide drain on every turn — that would couple unrelated conversations.
  The single-worker executor is only for FIFO ordering / avoiding
  worker-side `Future.result()` chaining deadlocks; the barrier key remains
  `conversation_id`.
- **Prompt-injection mitigations are partial — measure with multi-run probes.**
  A single golden PASS (or 3/3) is not enough on this free LLM endpoint; use
  `scripts/probe_injection.py --runs 15` (no retry harness) and report
  `injection_leaks`, not just path_ok. Scrub heuristics are narrow by design —
  do not expand them into a general content filter that eats legitimate policy
  prose. Web results get the same fence+scrub as policy chunks
  (`build_web_answer_prompt`).
- **Query-norm must not corrupt Phase 5 web-search entities.** Corpus-vocab
  SymSpell will invent near-misses for OOV tokens (observed: Niva→five,
  Compare→company at edit distance 2). Keep default max edit distance at **1**,
  skip Capitalized OOV tokens, and never feed the normalized string into the
  web decision / tool query — those stay on the original question. Regression:
  `test_query_norm_preserves_entities_for_web_path` +
  `test_normalizer_preserves_external_entity_names`. Do not raise
  `QUERY_NORM_MAX_EDIT_DISTANCE` to 2 without re-running those cases against a
  vocab that contains "company"/"five"/"rated".
- **Phase 17 measure harness ≠ ARCHITECTURE #18–24 corpus.**
  `scripts/measure_query_normalization.py` seeds golden CORPUS + wellness
  (+ optional `--noisy` pads). It proves spelling fires and can recover cosine;
  it does not claim to have reproduced the live Notion mid-pool ranking.
- **Retrieval-reuse threshold reasoning (Phase 8) — 0.72, and why cosine can't do
  better here.** Measured on BGE-M3 (query-vs-chunk cosine, same modality as the
  §-below gate bands): a legitimate *same-chunk* follow-up ("...and how many of
  those carry over?" vs the annual-leave chunk) scores ≈ **0.63**, yet a genuinely
  *new-info* adjacent-topic follow-up ("how many sick days?" vs that same leave
  chunk) scores ≈ **0.67** — i.e. the case we MUST retrieve fresh for outscores a
  case we'd have been happy to reuse. **No single cosine threshold separates them**
  (this is the same tiny-margin trap as the 0.35 gate, below). The costs are
  asymmetric: a *wrong* reuse skips the chunk that actually answers the question and
  forces a wrong "I don't know", while a *missed* reuse only costs one redundant
  retrieval. So the threshold is set ABOVE the highest observed new-topic score
  (~0.67) at **0.72**: only near-verbatim repeats/clarifications of the same fact
  (~0.72–0.77, e.g. an observed live reuse at 0.724) fire; everything else retrieves
  fresh. Consequence to expect: **reuse fires rarely on a small policy corpus** —
  that's by design (correctness over the optimization), not a bug. Like 0.35 this is
  a *starting point* to validate against logged production similarities + a reuse
  hit/miss audit, NOT a final value; do not lower it to force more reuse without
  that data. Re-measure if the embedding model changes.
- **The reuse check stores chunk TEXT, not embeddings, and recomputes them
  (Phase 8).** `conversation_last_retrieval` holds only `{content, document_id,
  chunk_index, org_id}` as JSON — no vector column. The next turn re-embeds those
  few chunk texts locally (BGE-M3, deterministic, ~ms) to score them against the new
  question. Tradeoff chosen deliberately: a tiny recompute avoids adding a
  vector-array column and keeps the decision reproducible. Only the *latest* turn's
  chunks are kept (one upserted row per conversation) — the check only ever looks
  one turn back. Web/fallback answers store an empty list, so they're never reused.
- **The reuse check must never bypass the confidence gate (Phase 8).** `_try_reuse`
  returns a `gate_score` that is a real cosine of *this* question vs the reused
  chunk, and the reused hits go through the identical gate → strict-prompt → generate
  path. Don't "shortcut" a reused turn straight to an answer — if the reused chunks
  don't actually clear 0.35 / answer the question, they must be refused exactly as a
  fresh retrieval would be. Reuse only ever *saves the retrieval work*, never the
  grounding checks.
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
| `documents`     | A source policy file/upload, scoped to one org. `id`, `org_id`, `title`, `source_uri`, `created_at`. As of Phase 4, `source_uri` is populated with the origin URL (e.g. the Notion page URL) at ingest. Incremental sync also carries `source_external_id` / `source_last_modified` / **`source_provider`** (e.g. `notion`\|`google`) — unique on `(org_id, source_provider, source_external_id)` so Notion and Google corpora coexist without wiping each other. |
| `chunks`        | Text chunks + their `vector(1024)` embedding, scoped to one org. `id`, `org_id`, `document_id`, `chunk_index`, `content`, `embedding`, `created_at`. Phase 6: `content_tsv` (a `tsvector` GENERATED from `content`, GIN-indexed) powers keyword/hybrid search; `content` may include a prepended contextual-retrieval prefix. |
| `conversations` | (Phase 5) A conversation, scoped to one org. `id`, `org_id`, `summary` (running compression of pruned older turns), `created_at`. |
| `conversation_turns` | (Phase 5) One question+answer within a conversation. `id`, `conversation_id`, `org_id`, `turn_index`, `question`, `answer`, `created_at`. Older turns are pruned once folded into the summary (Phase 8: incrementally, one at a time). |
| `conversation_last_retrieval` | (Phase 8) The chunks retrieved on a conversation's most recent turn, for the pre-retrieval reuse check. One upserted row per conversation: `conversation_id` (PK), `org_id`, `chunks` (TEXT holding a JSON array of `{content, document_id, chunk_index, org_id}` — no embeddings), `updated_at`. |
| `users` | (Phase 10) An application user. `id`, `email` (UNIQUE), `org_id` (nullable — but never issued a session while null), `role` (`admin`\|`member`), `created_at`. Phase 21: `sessions_revoked_at` — sessions with JWT `iat` ≤ this timestamp are rejected. |
| `oauth_connections` | (Phase 10) One org's OAuth credential for one provider. `id`, `org_id`, `provider`, `external_workspace_id`, `external_workspace_name`, `access_token_encrypted`, `refresh_token_encrypted`, `expires_at`, `connected_by_user_id`, `created_at`. `UNIQUE (org_id, provider)` — one row per org per provider, so a lookup can never be cross-tenant-ambiguous. Tokens are encrypted via `app/security/crypto.py`; this table never stores plaintext. Google Integration: optional **`source_config` JSONB** (e.g. `{folder_id, folder_name}`) — preserved on reconnect (upsert does not clobber it). |
| `ingestion_jobs` | (Phase 10/12) A durable, pollable record of an admin-triggered fetch→chunk→embed→store run. `id`, `org_id`, `connection_id`, `status` (`queued`\|`running`\|`succeeded`\|`failed`), `doc_count`, `error`, `started_at`, `finished_at`, `created_at`. Consumed by a Postgres-backed worker (`SELECT ... FOR UPDATE SKIP LOCKED`), not an in-process background task. |
| `magic_link_tokens` | (Phase 13) Single-use employee login tokens. `token_hash` (PK — only a SHA-256 hash is ever stored, never the token), `email`, `expires_at`, `consumed_at`, `created_at`. |
| `oauth_states` | (Phase 13) Single-use, server-side OAuth `state` values for CSRF/replay protection on the admin connect flow. `state` (PK), `org_id`, `provider`, `expires_at`, `consumed_at`, `created_at`. |
| `query_answer_cache` | (Phase 19) Short-TTL cache of standalone Q→A results keyed by `(org_id, normalized_question_hash)`. Workspace-within-a-Workspace: the hash input folds in `workspace_id` (no new column) so an org-wide and a workspace's cache entry for the same question text never collide. |
| `api_rate_counters` | (Phase 21) Sliding-window request counters for Postgres-backed rate limiting (`scope` PK, `window_start`, `count`). |
| `workspaces` | (Workspace-within-a-Workspace) An employee-created sub-workspace nested inside one org. `id`, `org_id`, `name`, `created_by` (nullable, `ON DELETE SET NULL`), `created_at`. |
| `workspace_members` | (Workspace-within-a-Workspace) Membership in a sub-workspace — a SEPARATE, stricter boundary than org membership (every member must already be a `users` row in the same org, enforced in `app/workspaces/`, not by a DB constraint alone). `workspace_id`, `user_id`, `role` (`owner`\|`member`), `invited_by` (nullable, `ON DELETE SET NULL`), `joined_at`. PK `(workspace_id, user_id)`. |
| `org_signup_requests` | (Signup-approval queue, §2/§4) A pending/approved/rejected request to create a new org, replacing the old immediate self-serve org+admin creation. `id`, `email`, `company_name`, `status` (`pending`\|`approved`\|`rejected`), `reject_reason`, `org_id` (nullable, `ON DELETE SET NULL` — populated only on approval, an audit trail of which org a request became), `reviewed_at`, `created_at`. Partial unique index `idx_org_signup_requests_email_pending ON (email) WHERE status='pending'` — one pending request per email; re-submitting after a rejection is allowed. Reviewed only via `scripts/review_signup_requests.py` (no HTTP surface). |

**`org_domains` (Phase 10) was dropped** in the domain-auto-join simplification
(see §2) — `DROP TABLE IF EXISTS org_domains` in `schema.sql`. It held a
company email domain claimed by an org (`domain` UNIQUE, `auto_join_enabled`,
plus a `verified_at` DNS-check column removed even earlier); nothing reads it
any more. Restore it from git history if self-serve domain auto-join is
revived.

Deletes cascade: removing an org removes its documents, chunks, users,
oauth_connections, and ingestion_jobs (and its conversations + turns +
last-retrieval row); removing a conversation removes its turns and its
last-retrieval row; removing a workspace removes its members, and (via
`ON DELETE CASCADE` on the new `workspace_id` columns) its scoped documents,
chunks, conversations, oauth_connections, and ingestion_jobs. Indexes: `org_id`
on every org-scoped table (tenant filter) + an HNSW cosine index on
`chunks.embedding` (ranking speed) + `workspace_id` indexes on every table that
carries it. **Exception:** `org_signup_requests.org_id` is `ON DELETE SET
NULL`, not `CASCADE` — deleting an org nulls out `org_id` on its (approved)
request row rather than deleting it, so the request stays as an audit trail;
test cleanup that deletes an org must separately delete any
`org_signup_requests` rows it created (see `signup_email_cleanup` in
`tests/conftest.py`).

**Phase 7 added no tables** — the PolicyAgent and golden-set eval are pure
application/tooling layers over the existing schema. **Phase 8 added one table**
(`conversation_last_retrieval`) for the retrieval-reuse check and removed the
`MEMORY_SUMMARIZE_AFTER` setting (summarization is now incremental). **Phases
10-13 added the six tables above** — the first `users`/`auth`/OAuth tables in
the project, closing the gap Phase 9 explicitly deferred. **Workspace-within-
a-Workspace added two tables** (`workspaces`, `workspace_members`) and a
nullable `workspace_id` column on every table that already carried `org_id`
for content/credential/job scoping (`documents`, `chunks`, `conversations`,
`conversation_turns`, `oauth_connections`, `ingestion_jobs`, `oauth_states`) —
see §2 for the full reasoning. **The signup-approval-queue change added one
table** (`org_signup_requests`) and no new columns on any existing table.

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
  the page; an unanswered one falls back. Scripts: `ingest_notion.py` (+ `ask.py`,
  retired in Phase 9 → `cli.py`).
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
  data. Scripts: `chat.py` (multi-turn) + `ask.py` — both retired in Phase 9,
  replaced by the single interactive `cli.py`.
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
- Phase 8 — Two refinements to the conversation/retrieval path, gate + grounding +
  web-search untouched. (A) **Incremental summarization** (`app/rag/pipeline.py`
  `_update_running_summary`): the running summary is updated after *every* turn by
  folding in only the single turn that just left the verbatim window
  (`existing summary + one turn` → one small LLM call), replacing Phase 5's
  bulk-at-threshold summarize. Window `MEMORY_RECENT_TURNS` 4→3; `MEMORY_SUMMARIZE_AFTER`
  removed. Same `conversations.summary` + `conversation_turns` storage. (B) **Retrieval
  reuse** (`_try_reuse`): a cheap, deterministic, *non-LLM* cosine check before
  retrieval — if the rewritten question is close enough (`RETRIEVAL_REUSE_THRESHOLD`
  = 0.72) to the previous turn's chunks, reuse them and skip hybrid search + rerank;
  else retrieve fresh. The reuse similarity becomes `top_score`, so reused chunks pass
  through the **unchanged** gate → strict-prompt → generate path (never bypassing it).
  Previous chunks persist in a new `conversation_last_retrieval` table (text + locator,
  no embeddings — recomputed on demand). Observable via `RagResult.retrieval_reused` /
  `AgentResponse.retrieval_reused`. Tests: `test_incremental_summary.py` +
  `test_reuse.py` (deterministic unit fakes for the decision logic + a real-BGE-M3
  threshold-separation check) and an updated incremental integration test in
  `test_conversation.py`; full suite green (39 passing, 2 network deselected). Verified
  live against real Notion data (`scripts/demo_phase8.py`): incremental summary set
  from turn 4 and updated each turn (verbatim capped at 3, early-turn context resolved
  from the summary), and a reuse firing at 0.724 while lower-similarity turns retrieved
  fresh. **Finding:** on a small policy corpus cosine cannot cleanly separate "same
  fact" from "adjacent topic" (a same-chunk follow-up ≈0.63 can score *below* a
  new-topic one ≈0.67), so reuse is deliberately conservative and fires rarely —
  correctness over the optimization (see §4).
- Phase 9 — The closing phase of this build stage: a clean interface + per-org
  credential plumbing, no new RAG behaviour. (A) **Single interactive CLI**
  (`scripts/cli.py`): pick/pass an org, then a back-and-forth loop until `/exit`,
  built with `rich` (presentation-only dep, isolated to the script). A thin shell
  over the Phase 7 `PolicyAgent` — it drives `agent.answer` and formats the result,
  surfacing each turn's internals (rewrite / retrieval reuse / provenance / grounding
  citations). Retired `ask.py` + `chat.py` (it replaces both). Added a small optional
  `VectorStore.list_organizations` for the org picker. (B) **Per-organization Notion
  credentials**: each org gets its own `NOTION_TOKEN_<NAME>` secret; `NotionSettings`
  discovers them generically and `resolve_token(name)` returns only that org's token
  with **no** fallback, so the org boundary is enforced by Notion (an integration
  only sees pages shared with it), not just our code. `ingest_notion.py` gained
  `--org`/`--token`; adding an org later is one env var + one run, no code change.
  Tests: `test_cli.py` (CLI drives the agent with a stable conversation id, stops on
  exit, renders internals) + `test_notion_credentials.py` (a run uses the specific
  token, never another org's or the global one) — 10 new, offline/deterministic; full
  suite green (49 passing, 2 network deselected). Verified the CLI live against the
  existing Phase 4 Notion org. **Explicitly NOT in this phase / next stage:** frontend,
  HTTP API, OAuth flow, admin/user-role handling — and the real multi-org Notion data
  entry + ingestion (this phase only lays the credential foundation for it).

- Bounded retrieval recovery + Grounding Gap prompt — Extends ``RagPipeline``
  without replacing hybrid/RRF/rerank/gate/memory/web. First retrieve unchanged;
  at most one recovery when evidence is insufficient (``gate_miss`` or generation
  insufficiency). ``build_recovery_queries_prompt`` + three-mode
  ``build_grounded_prompt``. ``RecoverySettings``; diagnostics on ``RagResult`` /
  ``AgentResponse`` / CLI. Tests: ``tests/test_recovery.py`` (generic categories,
  fakes) + adjusted grounding related-but-not-explicit case. Existing fixtures
  disable recovery so prior suites stay deterministic.
- Phase 15 — Deferred running-summary fold off the critical path
  (`app/rag/summary_fold.py`). Confirmed the review claim: `_update_running_summary`
  used to block `answer()` after the result was known. Now scheduled in the
  background; rewrite barrier is **per-`conversation_id`** via
  `_pending[conversation_id]` (not a global queue wait). Tests:
  `test_summary_fold_deferral.py` + updated incremental/conversation drains.
  Live measure: turn-4 answer ~1.5s vs fold ~1.7s in background. Branch:
  `fix/async-summary-fold`.
- Phase 16 — Indirect prompt-injection mitigation (partial). Fence + untrusted
  rules on grounded / contextualize / recovery / web prompts; heuristic scrub
  in `app/security/untrusted.py`. Golden injection cases +
  `test_prompt_injection_structure.py` (incl. contextualize *output* hijack) +
  `test_untrusted_scrub.py`. 15-run probe: fencing alone ~40% sabbatical pass
  with leaks; +scrub → 0 measured leaks on that payload. Branch:
  `fix/prompt-injection-mitigation`.
- Phase 17 — Corpus-vocab SymSpell query normalization before embed/retrieve
  (`app/rag/query_normalize.py`, `QueryNormSettings`, `list_chunk_texts`).
  Generation/web keep the unresolved question. Guards: English seed, inflection
  skip, max edit distance 1, Capitalized OOV skip (protects Phase 5 entities).
  Tests: `test_query_normalize.py` + web-path entity interaction; recovery typo
  test disables query-norm so it stays isolated. Harness:
  `scripts/measure_query_normalization.py` (honest about #18–24 corpus gap).
  Branch: `fix/query-normalization`.
- Phases 10-14 — The product layer Phase 9 explicitly deferred: real per-org OAuth,
  an admin panel, a durable ingestion queue, an HTTP API, streaming chat, and a
  frontend portal. See §2 for the full architectural reasoning; summary per phase:
  - **Phase 10** (identity/OAuth schema + encryption): `users`, `org_domains`,
    `oauth_connections`, `ingestion_jobs` tables; `app/security/crypto.py`
    (MultiFernet token encryption, key rotation, no external KMS);
    `AuthSettings`/`ApiSettings`/`EmailSettings`. Tests: `test_security.py`.
  - **Phase 11** (OAuth provider abstraction): `app/auth/` `OAuthProvider` +
    `NotionOAuthProvider` + factory + DB-backed connection credentials
    (`credentials.py`), kept fully independent of the legacy env-var token path
    (no fallback between them). Tests: `test_auth.py`.
  - **Phase 12** (ingestion job queue): `app/jobs/` Postgres-backed durable queue
    (`FOR UPDATE SKIP LOCKED` claim, stuck-job reaper) + worker running the
    unchanged `ingest_source()`; `scripts/run_worker.py`. Tests: `test_jobs.py`.
  - **Phase 13** (streaming + HTTP API, four sub-phases a-d): `RagPipeline.answer_stream`
    / `PolicyAgent.answer_stream` (chunks an already-decided answer — see §2 for why
    NOT raw token streaming); `app/api/` FastAPI app — magic-link auth
    (originally DNS-verified opt-in domain auto-join, **since replaced by
    direct admin-invited members, see §2/§5**), admin OAuth connect, admin
    connections/jobs endpoints (every route org-scoped from the session, never
    client input), SSE chat streaming with client-supplied `conversation_id`
    verified against the caller's org. Tests: `test_streaming.py`, `test_api_auth.py`,
    `test_identity.py`, `test_api_admin.py`, `test_api_chat.py`.
  - **Phase 14** (frontend): `frontend/` Next.js 15 App Router portal — magic-link
    login/verify, streaming chat with provenance-coded citations, admin
    members/connections/jobs panels (originally domains/connections/jobs — the
    domains panel was replaced by a member-invite panel, see §2). Provider-agnostic
    `ConnectionCard` (Google/GitHub render "coming soon" through the same
    component Notion uses).
  - Full suite after Phase 14: 128/129 passing, 2 network deselected (the one
    failure is pre-existing environmental `NOTION_TOKEN_SYVORA` pollution in a
    developer's local `.env`, reproduced identically on the pre-Phase-10 commit —
    not a regression from this work).
  - **Not done in this stage** (see Pending below): a live end-to-end walkthrough
    against a real sandbox Notion OAuth app + deployed frontend/backend (needs
    real OAuth app credentials this environment doesn't have); production secrets
    (`AUTH_JWT_SECRET`, `AUTH_ENCRYPTION_KEYS`) are config knobs, not generated/
    provisioned by this work.

**Auth simplification (post-Phase-14) — domain auto-join removed, replaced by
admin-invited members.** See §2 for the full reasoning (deferred as premature
for the current small-known-user-base stage, not a design mistake) and §5 for
the dropped `org_domains` table. Changes: `app/auth/domains.py` deleted;
`app/auth/users.py` gained `invite_member`/`list_members`; `POST /admin/members`
+ `GET /admin/members` replace the domain endpoints; `request_magic_link`
now only ever links an email with an existing account (signup or invite),
never auto-creates one; frontend `admin/domains` page replaced by
`admin/members` (invite-by-email form); `tests/test_domains_and_identity.py`
replaced by `tests/test_identity.py`. Verified live end-to-end against the
real dev DB (signup → invite → magic-link → login lands in the inviting
admin's org as `role=member`; an uninvited email gets the same generic
response but no account is ever created; a second org's admin never sees the
first org's invited members). Full suite green (144 passing, 1 pre-existing
unrelated `NOTION_TOKEN_SYVORA` environmental failure, 2 network deselected).

**Signup-approval queue (branch `feature/signup-approval-queue`) — self-serve
org+admin creation replaced by manual platform-owner review.** See §2/§4/§5
for the full reasoning and schema. Changes: new `org_signup_requests` table +
`app/auth/signup_requests.py` (create/get/list/approve/reject); `POST
/auth/signup` (`app/api/auth.py`) now only queues a pending request instead
of calling `store.create_organization`/`create_admin` directly, and no
longer returns `dev_link`; two new email templates
(`send_signup_approved_email`/`send_signup_rejected_email` +
`_safe` wrappers) in `app/auth/email.py`, which also had its console/smtp
dispatch factored into a shared `_dispatch()` helper so the two new
templates and the existing magic-link one don't each re-implement it;
`scripts/review_signup_requests.py` (`list`/`approve`/`reject`) is the
platform owner's only interface to the queue — no new HTTP/session surface.
Frontend `signup/page.tsx` copy updated for "pending review" instead of
"check your inbox"; `api.signup()` now returns a dedicated `SignupResponse`
(no `dev_link`) rather than the shared `MagicLinkResponse`. Tests: replaced
the old immediate-admin HTTP test in `test_api_auth.py` with pending-request
assertions + duplicate-pending + re-request-after-rejection cases; new
`tests/test_signup_requests.py` covers the module directly (create/get,
duplicate-pending rejected, approve creates org+admin, reject records a
reason, double-approve/double-reject raise `NotFoundError`, re-request after
rejection succeeds, `list_signup_requests` pending-vs-all). Manually
smoke-tested the CLI end-to-end against the local dev DB (list → approve →
verified org+admin via `get_user_by_email` → reject with reason → console
email printed). Existing invited-member and magic-link login paths are
completely unchanged.

**Hardening pass (Phases 18–22, external review follow-up).** Phase 20
(structural citations + NLI) is **explicitly deferred** pending a separate
cost/latency decision — not summarized as done below.

- **Phase 18** — Token-based chunking (`CHUNK_SIZE`/`CHUNK_OVERLAP` in tokens via
  BGE-M3 tokenizer in `app/ingestion/chunk_tokens.py`); real Okapi BM25 re-ranking
  over FTS-filtered candidates (`app/vectorstore/bm25_ranking.py`, gate still uses
  cosine top-1); compound-question decomposition (`app/rag/decompose.py`) with
  per-sub-question retrieve + merge before rerank. Branches: `improve/chunking-bm25-decomposition`.
- **Phase 19** — Request deadline (`app/rag/request_budget.py`); aux LLM for
  rewrite/decompose/recovery/summary/ingest-context (`LLM_AUX_MODEL`); structured
  token logging (`rag.llm_usage`); Postgres `query_answer_cache` for standalone
  questions (`app/rag/query_cache.py`, `RagResult.cache_hit`). Branches:
  `improve/latency-cost-controls`.
- **Phase 21** — Postgres-backed chat rate limits (`app/security/rate_limit.py`,
  `api_rate_counters`); session revocation via `users.sessions_revoked_at` +
  JWT `iat` check in `get_session` + `POST /admin/members/{id}/revoke-sessions`;
  ingestion sanitization (`app/ingestion/sanitize.py`, size + control-char ratio).
  Branch: `improve/security-hardening`.
- **Phase 22** — Fast retrieval-only eval (`evaluation/retrieval_eval.py`, rank of
  correct chunk, no LLM; wired into CI no-LLM tier); production query signals
  (`rag.query_signals` JSON: `top_score`, `response_mode`, `answered`, `source`,
  `retrieval_reused`, `cache_hit`, etc. from `RagPipeline.answer`). Branch:
  `improve/eval-split-production-signals`.

**Workspace-within-a-Workspace** (branch `feature/workspace-within-workspace-clean`,
plan: `docs/plans/2026-08-03-workspace-within-workspace.md`). An authenticated
employee can create a personal sub-workspace inside their org, invite a few org
colleagues, connect their own Notion/Drive source, and have questions asked *in
that workspace* answered only from its own content — never blended with the
org-wide policies, never crossing into a sibling workspace. Built as one new
nullable `workspace_id` axis nested inside `org_id`, threaded through the
existing pipeline rather than a second, parallel system (full reasoning in §2).
Layers, each additive and gated behind `workspace_id` defaulting to `None`
(zero behavior change for every existing org-wide call site):
- **Schema**: `workspaces` + `workspace_members` tables; `workspace_id` on
  `documents`/`chunks`/`conversations`/`conversation_turns`/`oauth_connections`/
  `ingestion_jobs`/`oauth_states`; `oauth_connections` uniqueness re-keyed to
  two partial indexes (org-wide vs. per-workspace).
- **`app/workspaces/`** — CRUD + membership, its own boundary stricter than org
  membership (`assert_member` checks `workspace_id` + `org_id` + `user_id`
  together).
- **`VectorStore`** (`query`/`keyword_search`/`list_source_documents`/
  `upsert_source_document`/`acknowledge_source_document`/`delete_source_documents`)
  — optional `workspace_id`, scoped via `IS NOT DISTINCT FROM` so `None` still
  matches org-wide `NULL` rows exactly as before.
- **`RagPipeline`/`HybridRetriever`/`PolicyAgent`** — optional `workspace_id`
  threaded to every retrieval call site + the query-answer cache key; the
  gate/strict-prompt/reranker/memory logic is completely unchanged.
- **OAuth connect flow** (`app/auth/oauth_state.py`, `credentials.py`,
  `app/api/auth.py`) — `?workspace_id=` on `authorize`, restricted to the
  workspace's `owner`; `oauth_states`/`oauth_connections` carry it through to
  the callback.
- **Ingestion** (`app/ingestion/pipeline.py`, `app/jobs/`) — sync state
  (new/updated/removed) is now diffed independently per workspace, mirroring
  exactly how it's already partitioned per provider.
- **HTTP API** — `app/api/workspaces.py` (create/invite/connections/ingest/
  jobs, gated by `deps.get_workspace_role`/`require_workspace_owner`);
  `/chat/stream` + `/chat/conversations` accept an optional `workspace_id`,
  and a client-supplied `conversation_id` is now checked against BOTH
  `org_id` and `workspace_id` before use.
- **Frontend** — `frontend/app/workspaces/` (list/create) +
  `workspaces/[id]/` (members, invite, connect, jobs); `/chat` accepts
  `?workspace=<id>` (same component, not a forked chat UI).

Verified: new isolation/RAG/API test files (`test_workspaces.py`,
`test_workspace_rag.py`, `test_api_workspaces.py`) plus extensions to
`test_isolation.py`/`test_auth.py`/`test_jobs.py`/`test_incremental_sync.py`/
`test_api_auth.py`/`test_api_chat.py`; full suite green each iteration
(only the pre-existing `NOTION_TOKEN_SYVORA` env-pollution failure and
known free-LLM-endpoint flakiness observed, both documented above, not
regressions); CI (`golden-set-eval`) green; a live end-to-end smoke test
against a running API (signup → magic-link login → create workspace → list
→ members → workspace-scoped conversation) all returned correct responses;
`npm run build` + `tsc --noEmit` clean for the frontend. Caught and fixed a
schema-ordering bug during this work (see §2/§4) that only reproduced on a
genuinely fresh database, not an already-migrated one.

**Backlog (deliberately unscheduled this round — do not drop silently):**
- HNSW index build/query parameter tuning (`m`, `ef_construction`, `ef_search`) —
  matters at corpus scale not yet reached.
- LLM provider-level prompt caching for the large fixed grounded-prompt prefix —
  lower priority next to query-result cache (Phase 19).

**Google Drive/Docs integration (on `feature/google-integration`).** Second
external source alongside Notion — Phases 1–7 of `GOOGLE_INTEGRATION_PLAN.md`:
provider-partitioned sync, live token refresh, `GoogleOAuthProvider`,
per-connection folder config (storage + admin PUT/GET + Drive `files.get`
validation), `GoogleDriveAdapter` (native Docs via markdown export + folder
BFS), factory/worker/changes wiring, and frontend (Sources + onboarding treat
Google as a first-class connect). Gate/prompt/retrieval untouched. Live OAuth
walkthrough against a real internal-use Google client is still pending.

**Pending (not started)**
- **Live end-to-end verification of Phases 10-14** against a real sandbox Notion
  OAuth app (create one, set `NOTION_CLIENT_ID`/`SECRET`/`REDIRECT_URI`) and a
  deployed frontend + API — everything above was verified via the automated
  test suite + local `npm run build`, not a live walkthrough (domain-based
  employee onboarding no longer applies — see the auth simplification above).
- **Live Google Drive walkthrough** (connect → paste folder → sync → ask →
  edit Doc → change-check → re-sync; plus Notion+Google coexistence on one org)
  against an internal-use Google Cloud OAuth client (`GOOGLE_CLIENT_ID` /
  `SECRET` / `REDIRECT_URI`). Automated tests cover the plumbing offline.
- Real multi-organization data entry now has THREE paths: the Phase 9 manual
  `NOTION_TOKEN_<NAME>` + `ingest_notion.py --org … --token …` script (still works,
  unchanged), the Phase 10-14 Notion OAuth self-serve flow, or Google Drive
  OAuth + folder config in the portal. Neither live Google nor production
  company data has been run end-to-end in this environment yet.
- Production secrets management: `AUTH_JWT_SECRET` and `AUTH_ENCRYPTION_KEYS` must
  be generated and provisioned per environment (e.g. a secrets manager) before any
  real deployment — this work defines the config surface, not the provisioning.
- Email delivery is `console` (prints the link) by default; a real deployment needs
  `EMAIL_SENDER=smtp` configured, or a transactional-email provider swapped in
  behind `app/auth/email.py`.
- Validate the Phase 8 reuse threshold (0.72) and the 0.35 gate using **production
  `rag.query_signals` logs** (Phase 22) plus a reuse hit/miss audit — no longer
  only hand-measured examples. A richer reuse signal (e.g. comparing the rewritten
  question to the previous question) remains a future experiment.
- Act on the Part 3 gate findings — a *decision*, not a default: the evidence says
  keep `0.35` and the two-layer design as-is (`evaluation/reports/GATE_FINDINGS.md`).
  Any future recalibration must be driven by an *expanded* golden set + production
  signal logs, never the current ~17-case sample. Awaiting explicit sign-off.
- **Phase 20 (deferred):** structural `{claim, chunk_id}` citations + cheap NLI
  per claim — revisit after weighing latency/cost using Phase 19 token logs.
- RAG enhancements: token-budget-aware context assembly and structured
  (machine-readable) citations. Current pipeline returns `sources` for
  traceability and asks the model to cite `[n]` inline, but does not yet parse
  citations out or trim context to a token budget.
- More source adapters, implementing the same `SourceAdapter` interface: Google
  Sheets, Drive-hosted PDF/DOCX, GitHub, Slack. (Notion + native Google Docs done.)
- Incremental sync is implemented: Sources page change-check
  (`GET /admin/connections/{id}/changes`) compares remote `last_modified` to
  stored `documents.source_last_modified` **per provider**; "Update policies"
  upserts only new/changed pages (and drops removed ones) via
  `(source_provider, source_external_id)` — no duplicate dumps / no cross-provider
  wipe. First-time onboarding ingest uses the same path.
- Ingestion adapters: layout-aware extraction from PDF/DOCX/HTML.
- Packaging the self-hosted Docker image.
- Still-open items from the hardening review (not in Phases 15–17): global
  request deadline / cancellation, Postgres RLS as defense-in-depth beyond
  application `org_id` filters, token-budget-aware context assembly, structured
  (machine-readable) citation verification, and any model-routing / token-
  accounting work. Token-budget + structured citations already listed under
  RAG enhancements above.

---

_When you finish a phase: update sections 4, 5, and 6 (and 2/3 if conventions
changed) before committing._
