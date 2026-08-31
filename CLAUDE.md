# CLAUDE.md — project rulebook

> Standing guide for every phase. **Update it at the end of each phase.**
> One dense line per fact, with a file path — long-form history lives in git.

## 1. What this is

A **multi-tenant RAG platform for company Q&A over connected sources**
(Notion, Drive, Slack, Linear, GitHub). Tenants connect their tools; their
employees get answers grounded in *their own* data.

- **RAG, not fine-tuning** — facts change; re-ingesting beats retraining.
- **Strict tenant isolation** — one org must never see another's content.
- **Goal: a self-hosted Docker image** — prefer components that run locally
  with no paid dependency.

## 2. Conventions (match these, don't reinvent)

- **New capability = new package**: `base.py` (contract) + impl(s) +
  `factory.py` + `__init__.py`. Depend on the interface and `build_*()`,
  never a concrete class.
- **Exception:** an *orchestrator* composing existing interfaces (`app/rag/`,
  `app/ingestion/`, `app/schedulers/`) skips `base.py` — no second backend to
  abstract over. Don't add one speculatively.
- **All config** is a frozen dataclass with `from_env()` in
  `app/config/settings.py`; nothing else reads env. No central aggregator —
  each factory calls `X.from_env()` on demand.
- **All failures** raise `ProviderError`/subclass (`app/core/exceptions.py`)
  with `cause=` and `raise ... from`.
- `from __future__ import annotations`; type hints; docstrings say **why**.
- **Bound every external walk** and **mark truncation** — a partial result
  that looks complete is the failure that matters.

## 3. Architecture — the load-bearing decisions

**Providers & storage** — plain OpenAI-compatible client, not LiteLLM
(switching LLM is `LLM_MODEL`/`LLM_BASE_URL`/key, no code change). Local
BGE-M3 embeddings by default with a `remote` backend behind the same
interface for deploys; same for `app/reranker/`. Postgres + pgvector via a
pool — `register_vector` runs once per physical connection in the `configure`
hook, and every process boundary must `close_pool()`.

**Isolation**
- `org_id` on every tenant table; every read/write requires one. Retrieval
  filters `WHERE org_id = …` *before* ranking, so isolation never depends on
  the vector index (`tests/test_isolation.py`).
- **Workspace-within-a-Workspace**: a nullable `workspace_id` nests *inside*
  `org_id`; `NULL` = org-wide. Always pair it with `org_id`, never alone. A
  workspace sees ONLY its own rows, never also org-wide ones — otherwise a
  meeting-notes space blending in HR policy makes membership meaningless.
  `workspaces/store.py::assert_member` is the one place it's validated.

**RAG query path (`app/rag/`)** — normalize → embed → retrieve → gate →
grounded generate → `RagResult`.
- **Two independent grounding layers:** a confidence gate
  (`RAG_SIMILARITY_THRESHOLD=0.35`) refusing *without calling the LLM*, and a
  strict prompt emitting the same fallback when context doesn't answer — a
  threshold can't separate "answers" from "on-topic but doesn't" (§5).
- **Retrieval** = contextual chunks + hybrid vector/BM25 fused with RRF
  (k=60, rank-based so no score normalization) + cross-encoder rerank of a
  30-candidate pool. **The gate is unchanged** — `gate_score` is still the
  best cosine, so these only reorder.
- **Memory**: a follow-up is rewritten standalone *before* retrieval, leaving
  the gate/prompt path untouched. The summary folds one turn at a time, off
  the critical path (barrier is per-`conversation_id`, not global).
- **Reuse** (`_try_reuse`): a non-LLM cosine check against the previous
  turn's chunks; on a hit skip retrieval, but still pass the gate.
- **Bounded recovery**: at most ONE retry with alternative queries on a gate
  miss. Never answers the question; never weakens grounding.
- **Web fallback**: on gate miss the model may call `web_search` for real
  *external* entities — one search, no loop, labelled `source="web"`.
- **Untrusted input is fenced + scrubbed** (`app/security/untrusted.py`) — a
  partial mitigation; measure with multi-run probes. The `SYSTEM` block marker
  must be **uppercase AND decorated** (`***SYSTEM***`, `[SYSTEM]`, `SYSTEM:`,
  `--- SYSTEM ---`): the bare word ate content twice (`Ecosystem`, then a
  plain "…RAG system…" sentence), deleting everything after it. Scrubbing to
  empty now returns empty, **not the original** — the old fallback fail-OPENed
  on the one input that is certainly an attack.

**Agents (`app/agent/`)** — `Agent.answer(...) -> AgentResponse`,
source-agnostic on purpose.
- One pinned agent **per source**, each a trivial `RagPipelineAgent` subclass
  pinning `source_provider` + a prompt profile. Never one blended corpus: an
  org may use two tools for unrelated content, and blending hides which one
  grounded the answer. `PolicyAgent` is a legacy fallback.
- **`GitHubAgent` is not RAG** — nothing is embedded, so grounding is
  *structural*: answers are composed only from tool output, and no tool call,
  a bad arg, or any failure returns the fixed fallback.
- **Routing is a deterministic LangGraph `StateGraph`** — one node per agent,
  a plain Python router. No LLM picks the agent. One tool round, never a loop.

**Sources (`app/sources/`)** — one `SourceAdapter` per source; format
conversion lives *inside* the adapter. Thin SDKs, never frameworks.
- **Provider-partitioned sync is mandatory** — every sync path takes an
  explicit `provider`, or the first Google sync deletes every Notion doc.
- **GitHub embeds nothing** (`app/githublive/`) — live bounded calls; its
  stored repo name+description+topics do retrieval's job.

**Product layer**
- `app/api/` is the ONLY place `org_id` enters a request — always from the
  signed session cookie, never client input (`deps.get_session`).
- Magic-link login only, single-use server-side tokens. New orgs go through a
  reviewed signup queue whose links **GET a page, POST to act** — a mail
  scanner must not be able to approve one.
- Ingestion is a durable Postgres queue (`app/jobs/`), no Redis/Celery. Chat
  "streaming" chunks an **already-decided** answer — recovery or the web path
  can still discard a gate-passing generation.
- `frontend/` keeps the session only in the httpOnly cookie. No
  Tailwind/UI-kit — plain CSS vars and global classes.

**Activity Scheduler (`app/schedulers/`)** — a member saves free-text intent
+ a cadence; each run fetches that service's activity since the last run,
hands it plus the prompt to an LLM, and emails the result. **Reads live,
embeds nothing** (the `app/githublive/` pattern).
- **Sources = only those with an "activity since T" primitive**: GitHub
  (`list_commits(since=)`), Slack (`history(oldest=)`), Linear
  (`filter:{updatedAt:{gt:…}}`). Notion/Drive are absent on purpose —
  `SourceAdapter` answers "does this exist / is it stale", never "what
  happened between T1 and T2". `SUPPORTED_PROVIDERS` **must equal** `_FETCHERS`.
- **The email is a notification, not the report.** Each run **saves** a
  `scheduler_reports` row first, then mails a short "ready" note (plain +
  HTML) linking to `/schedulers/reports/{id}` on the FRONTEND origin (a
  `SameSite=Lax` cookie won't reach the API host). So a send failure costs the
  nudge, not the run's work; `delivered_to` is stamped only on an accepted
  send, so "was it emailed?" is a fact. Everything displayed is **snapshotted**
  (prompt, provider, cadence, `space_name`) — re-resolving would rewrite
  history on an edit or rename. No excerpt of the report text in the mail: a
  two-line preview of a grounded summary reads as the whole answer.
- **Fetchers return an `ActivityDigest`**: structured items (summary + url) +
  coverage notes + prompt text. **The email renders links, never the model** —
  the prompt forbids writing URLs, so a fabricated link is impossible rather
  than discouraged. Every provider discloses what it checked, so a report
  can't imply coverage it lacked — **including item-count caps**: Slack's
  `max_messages` is split *per channel* (greedy spending let one busy channel
  starve every later one while the notes still claimed it was checked), and
  hitting a Slack/GitHub/Linear cap adds a note. All three sources page
  newest-first, so a cap only ever drops the oldest end of the window.
- **The row IS the queue entry**, but as a *due list*: a claimed row advances
  `next_run_at` and returns to `active`. `attempts` is capped in **both**
  `mark_run_failed` and `requeue_interrupted_running`.
- **Fetch/LLM failures retry; email failures don't** (work already done;
  retrying re-sends). `last_run_at` advances only on *success*, so a retry
  still covers everything since the last delivered report.
- **`last_run_at` IS the next window's start, so it is stamped BEFORE the run**
  (`worker.py` → `mark_run_success(covered_until=…)`), never `now()` at
  delivery — the LLM+SMTP latency is tens of seconds, and anything happening
  in it belonged to no window at all. Stamping early can only overlap by
  milliseconds (a visible duplicate), never skip.
- **No activity ⇒ the LLM is never called** (an empty context is where
  invention happens). One `try/except` **per scheduler** — the broad one is
  the point.
- Every route is member-level and scoped by `(org_id, user_id)` — a
  scheduler is personal.
- **Scope = the company or ONE space**: `schedulers.workspace_id` (NULL =
  org-wide) is chosen in the UI, membership-checked with `assert_member`, and
  the connection is resolved **within that scope only** — a space-scoped
  report must never fall back to the org connection. The row's scope wins at
  run time (`runner` passes `scheduler.workspace_id`). `/schedulers/connections`
  also returns every space the member is in, **including ones whose only
  sources aren't schedulable yet** — an empty `providers` list plus a
  `connected` list, so "Meeting notes has Drive, not schedulable" is visible
  instead of the space silently vanishing.
- The setup-chat endpoint stays **org-wide only** (it has no space slot); the
  page now uses explicit space/service/cadence dropdowns, since which
  connection is read is not a thing to infer from prose.

**Multi-Model Selection (`app/llm/routed.py`, `catalog.py`)** — a member picks
which model answers, on every prompt surface (chat composer, scheduler create,
setup chat). Default `auto` = the configured `LLM_MODEL`, so an untouched
dropdown is byte-identical to pre-feature behaviour.
- **The model is a PER-CALL value, never a constructor arg.** Agents are
  `lru_cache(maxsize=1)` singletons holding the BGE-M3 embedder + cross-encoder;
  keying that cache by model would load a second copy of the weights per choice.
  `RoutedLLMProvider` dispatches on a request-scoped `ContextVar` and satisfies
  `LLMProvider`, so `RagPipeline`, every agent and `schedulers/runner` learn
  nothing about selection. A ContextVar, not a threaded param, because the
  pipeline calls the LLM from ~6 places for one request.
- **`build_aux_llm_provider` is deliberately NOT wrapped.** That absence is what
  makes ingest contextualization unroutable — chunks authored by different
  models would be ranked against each other inside one index. The guarantee is
  structural, not a rule to remember. Embeddings/reranker likewise never switch.
- **Every OpenRouter call carries `provider.data_collection="deny"`** — a RAG
  prompt contains the tenant's retrieved private content, so it must never reach
  a provider that may train on or publish it. Per-request, not the account-level
  privacy toggle (one global switch governing every tenant, silently wrong the
  moment someone edits a dashboard). `require_parameters=true` makes tool
  support a server-side guarantee rather than a hand-kept flag.
- **Two backends, not one: OpenRouter AND Groq** (`catalog.ModelChoice.backend`).
  Quota is the binding constraint — OpenRouter free is 50 req/day account-wide,
  Groq's is thousands — so drawing from both means one being exhausted does not
  empty the picker. `provider`/`reasoning` are OpenRouter request *extensions*
  and are never sent to Groq. A model whose backend has no key is not offered,
  never silently answered on the default.
- **~5 hardcoded models, admitted by `scripts/verify_models.py`** —
  not a live `/models` fetch. A model id is not a capability, and free models
  rotate out without warning: all 5 originally catalogued OpenRouter ids and 2
  of 3 guessed Groq ids were already dead when first probed. The test sends
  production's `RAG_MAX_ANSWER_TOKENS`, without which a reasoning model passes
  unbounded and then returns EMPTY content in production.
- The `done` SSE event and `scheduler_reports.model` report the **resolved**
  model (`response.model`), never the word "auto" — under a provider fallback
  the served model differs from the requested one, and only the former is a
  fact. `null` on the default path: naming the deployment's model to every
  member is noise, not provenance.

## 4. Layout

```
app/config/   typed settings — the ONLY place env is read
app/core/     ProviderError hierarchy
app/{llm,embeddings,reranker,vectorstore,websearch}/  base + impls + factory
app/llm/      + routed.py (per-request model) + catalog.py (the 5 offered)
app/db/       schema.sql, connection.py (pool), migrate.py
app/ingestion/ preprocess, chunk, contextualize, pipeline  (orchestrator)
app/rag/      pipeline, prompts, retrieval, query_normalize, summary_fold, …
app/memory/   org-scoped conversation history + last-retrieval
app/sources/  SourceAdapter: notion, google_drive, slack, linear + factory
app/githublive/ GitHub's whole data path — live reads, no vectors
app/agent/    Agent + per-source agents + orchestration (LangGraph)
app/security/ crypto, untrusted (scrub), rate_limit, client_ip
app/auth/     OAuth providers, credentials, users, magic_link, session, email
app/jobs/     ingestion queue + worker + scheduler_queue
app/workspaces/ sub-workspace CRUD + membership (assert_member)
app/schedulers/ store, activity (live "since T"), prompts, runner, worker
app/api/      FastAPI — deps (session/org_id), auth, admin, chat, workspaces,
              orgs, schedulers
evaluation/ golden set + harness + RAGAS ([eval] extra) · scripts/ entrypoints
frontend/ Next.js 15 portal · tests/ pytest
```

## 5. Gotchas — each of these cost real debugging time

**Grounding / retrieval**
- **A successful ingest clears that org's `query_answer_cache`** (`worker.py`).
  Without it new content is invisible for the 300s TTL — the same question
  returns its pre-sync answer, which reads as "the sync did nothing".
- **`SLACK_MIN_THREAD_CHARS` was 40, now 15.** At 40 a real one-liner ("Deploy
  is frozen till Monday", 28 chars) was dropped from ingestion *and* from
  change detection, so the Sources check truthfully said "up to date" while the
  channel had new content. A filter that hides content also hides changes.
- **Retrieval has no recency preference** — "what was discussed recently?"
  ranks by cosine only, so an older, wordier thread outranks yesterday's
  message and the answer *looks* stale even when the new content is indexed.
  `DateRange` filtering exists but nothing infers recency intent yet.
- **A content-destroying scrub is invisible until you measure it.** A 2,004-char
  Slack post reached the LLM as 196 chars because `\bSYSTEM\b` matched the word
  "system"; the report summarised a detailed post as one sentence and looked
  like a weak model. Diagnose thin generations by printing what the prompt
  ACTUALLY received, not by upgrading the model.
- **A selected model that breaks the `MODE: A|B|C` tag silently disables the
  groundedness audit.** `_MODE_TAG_RE` (`pipeline.py:92`) is anchored at the
  start of the response, so a `<think>` block or a "Sure, here's…" preamble
  yields `mode=None` — and the audit at `pipeline.py:923` only runs for modes A
  and B. The answer still renders, so the lost layer is invisible. Mitigated by
  `reasoning: {exclude: true}` on every routed call, and gated by the
  verify script. Diagnose a "worse model" by checking the tag parsed, not by
  assuming the model is weak.
- **`query_answer_cache` keys on the selected model** (`query_cache.py`,
  read from the request ContextVar, not passed in). Without it one member's
  Gemini answer is served verbatim to another who explicitly asked on Claude —
  a wrong answer, not a slow one. Reading it inside `_question_hash` means a
  future call site cannot forget it.
- **OpenRouter's free tier is 20 RPM / 50 req/day (1,000 after a one-time $10),
  account-wide across all tenants** — which is why `auto` stays on the existing
  Gemini config: OpenRouter is only hit on an explicit selection. A `:free`
  model with `data_collection: "deny"` can also have ZERO eligible endpoints
  ("No endpoints found matching your data policy") — the correct outcome for
  tenant data, and it means that model cannot be offered.
- **Do NOT raise the 0.35 gate.** Bands overlap on a tiny sample (answerable
  0.54–0.74 vs on-topic-unanswered 0.46–0.52) and the golden set showed zero
  false negatives; the strict prompt does the fine discrimination. Never feed
  RRF scores or reranker logits into it — it's calibrated for cosine.
- **Reuse threshold 0.72 is deliberately conservative** — a same-chunk
  follow-up can score *below* a new-topic one, so reuse fires rarely by
  design. Costs are asymmetric: a wrong reuse forces a wrong refusal.
- Query-norm: max edit distance **1**, skip capitalized OOV tokens, never
  feed the normalized string to the web decision (distance 2 turned
  `Niva`→`five`). Only the *retrieval key* is normalized. Known gap:
  `list_chunk_texts` ignores `workspace_id` — vocab only, no content leak.

**LLM endpoint**
- **A 429 is a hard quota, not a blip.** The free Gemini tier is **15 rpm** —
  this explains most "flaky test" behaviour. `INGEST_CONTEXTUAL_CONCURRENCY=8`
  is wrong for it; set 1–2.
- The grounded prompt is ~2.3k tokens, 96% fixed prefix, already ordered for
  provider caching. **Never move CONTEXT/QUESTION earlier.**

**Memory / performance**
- **The BGE-M3 tokenizer was 325MB — 64% of a 512MB box.** Chunking uses a
  heuristic now (`CHUNK_TOKEN_BACKEND`) and `transformers` is out of the
  deploy image. Import heavy deps lazily *inside* functions — a module-level
  one cost 7.3s of boot and tripped Render's 5s health check.
- **`CHUNK_MAX_CHARS=4000`** — every splitter needs a linguistic boundary, so
  a base64 blob became one 48k-char chunk and a 400. Char caps can't be
  fooled; token estimates can.
- **Local BGE-M3 + reranker can hang a 16GB Mac.** Avoid `--reload`;
  `RETRIEVAL_RERANK_ENABLED=false` is the switch.
- `register_vector` per *checkout* cost 5.8 DB round trips per query (`/me`
  was 15); now once per physical connection. **Count round trips, don't time
  them** — wrap `Cursor.execute`, since `TypeInfo.fetch` uses its own.
- **Wrong-gauge bugs, twice:** `ru_maxrss` is a high-water mark, not current
  RSS (a gate on it latched closed forever — read `/proc/self/statm`, fail
  *open*); `reap_stuck` must measure **silence, not age**
  (`coalesce(progress_at, started_at)`) or it kills a healthy slow ingest.

**Queue / SQL**
- **`WHERE id IN (SELECT … SKIP LOCKED LIMIT n)` does NOT bound an UPDATE** —
  Postgres may re-evaluate the subquery (LIMIT 2 claimed all 5); use a CTE.
  `claim_next`'s `= (SELECT … LIMIT 1)` is a *scalar* subquery and is fine —
  never generalize it by pluralizing `=` into `IN`.
- **Cap requeue attempts.** Unbounded requeue turned one OOM job into a
  permanent crash-loop outage, twice; a bug now costs one failed job.
- **`ALTER TABLE … ADD COLUMN` must follow that table's `CREATE TABLE`** in
  `schema.sql` — invisible on a migrated DB, breaks a fresh one. Verify
  against a throwaway database. Use **partial** unique indexes where `NULL`
  means "org-wide"; Postgres treats NULLs as distinct in a plain `UNIQUE`.

**Sources**
- **A Slack channel rename is invisible to change detection** — no message id
  or `ts` moves, so "up to date" is correct — but `source_config.channel_names`
  is a SNAPSHOT, so every label (suggestion chips, report coverage notes,
  `#channel` in an activity item) stays stale forever. Both change-check routes
  now call `slack_utils.refresh_channel_names` (ids are stable across a rename,
  so only labels move) and return `renamed` so the client can drop its cached
  suggestions. It also runs **automatically** on every Slack ingest job and
  before every scheduled Slack report, so a rename reaches the labels without
  anyone pressing Check — the implementation lives in `app/sources/`, not
  `app/api/`, precisely because the worker and scheduler call it. A missing channel keeps its stored name — a name we once
  knew beats a bare id.
- **Per-org Notion tokens must NOT fall back** (`resolve_token` raises); a
  page must also be explicitly *shared* with the integration. Its block walk
  needs a **shared char budget** — unbounded nesting built an unbounded
  string *and* unbounded API calls before any size check ran.
- **Linear auth differs per credential**: personal key RAW, OAuth as
  `Bearer` — wrong = silent 401. Pass `updatedAt` as one `IssueFilter`
  variable; Linear renamed the inner scalar.
- Drive/GitHub **404 what a token cannot see** — say "not found or not
  accessible", never "deleted", and never retry it.
- **GitHub's `installation_id` on the redirect can be spoofed** — verify
  against `GET /user/installations`, persist from *that* response. Its token
  exchange needs `Accept: application/json`.
- **A workspace GitHub connect must never bind the org's installation**
  (compare installation *ids*, not account type), and a workspace with no
  GitHub must raise rather than fall back — don't add a fallback.
- **Never delete on one unverified listing** — `_sanitize_removals` refuses
  to drop >50% of known docs (above a 5-doc floor); a suspicious first sync
  retries once.

**Deploy / auth**
- **Render free blocks outbound SMTP.** Use `EMAIL_SENDER=sendgrid` (free
  Single Sender, any recipient); Resend's sandbox only reaches the account
  owner, and `_safe` wrappers swallow it so a send *looks* successful.
- **Don't point the browser at the API host.** `SameSite=Lax` won't cross
  origins: use the Next.js `/api` rewrite (`NEXT_PUBLIC_API_BASE_URL=/api`),
  and OAuth callbacks must use the frontend origin too.
- Supabase: the **Session** pooler (5432), not transaction mode (6543), which
  breaks once-per-connection `register_vector`.
- Rate limits must key on a **trusted** forwarded header — not
  `request.client.host` (a proxy = one global bucket), not a caller-controlled
  one (a fresh bucket per request).

**Tests**
- Three `test_jobs.py` worker tests are **pre-existing-broken** (their
  `FakeIngestResult` lacks `ingested_external_ids`) — not a regression.
- The Phase 3 `rag` fixture disables memory + web search on purpose.
- Tests asserting on a **real** LLM's free-form output are marked `live_llm`
  and deselected in CI (`-m "not network and not live_llm"`); the golden-set
  path-firing checks still gate on the real model.
- The suite is slow environmentally (remote DB + 15 rpm LLM): run the phase's
  own file; `pytest --collect-only` catches import breakage.

## 6. Tables (`app/db/schema.sql`)

`organizations` · `documents` (unique on `(org_id, source_provider,
source_external_id)`) · `chunks` (`vector(1024)` + generated `content_tsv`) ·
`conversations` / `conversation_turns` / `conversation_last_retrieval` ·
`users` · `oauth_connections` (encrypted tokens, `source_config` JSONB, two
partial unique indexes: org-wide vs workspace) · `ingestion_jobs`
(+`phase`/`attempts`/`progress_at`) · `magic_link_tokens` · `oauth_states` ·
`github_install_pending` · `query_answer_cache` · `api_rate_counters` ·
`workspaces` / `workspace_members` · `org_signup_requests` · `schedulers`
(scoped by `org_id` **and** `user_id`, unlike every other tenant table; `model` NULL = the configured default) ·
`scheduler_reports` (same `(org_id, user_id)` scoping; snapshots its labels
rather than joining, so an archived report survives a rename or a deleted
space — it cascades only from the scheduler, org and user).

- Deletes cascade from `organizations`/`workspaces`. **Exception:**
  `org_signup_requests.org_id` is `ON DELETE SET NULL` (audit trail), so test
  cleanup must delete those rows itself.
- **GitHub added no tables and no columns** — just an `oauth_connections` row
  whose `source_config` holds the installation + authorized repos. No GitHub
  `documents`/`chunks` exist; that absence proves "embed nothing" holds.
- `EMBEDDING_DIM` is coupled to `chunks.embedding` — change both together and
  re-ingest. `migrate.py` opens a **direct** connection, not the pool, since
  `register_vector` needs the extension to already exist.

## 7. State

**Built:** provider abstractions; pgvector store + isolation; the RAG path
(two-layer gate, hybrid retrieval, rerank, decomposition, memory, reuse,
bounded recovery); Notion/Drive/Slack/Linear ingestion; GitHub live reads;
per-source agents + LangGraph routing; golden-set eval in CI (+ nightly
RAGAS); identity/OAuth/admin/ingestion queue/HTTP API/streaming chat; Next.js
portal; Workspace-within-a-Workspace; signup-approval queue; injection,
latency, security and eval hardening; the Activity Scheduler; Multi-Model
Selection (OpenRouter, ~5 models, per-request routing).

**Pending / known gaps**
- Scheduler: Notion/Drive fetchers (Drive takes
  `modifiedTime > …`, Notion needs sort-desc + early stop; both report only
  *that* a doc changed); **email delivery is unverified — `console` only** (a
  failed send now costs only the notification: the report is stored and
  readable in-app either way).
- No live walkthrough against real Notion/Drive/GitHub OAuth apps; the GitHub
  one also settles **T3** (whether `state` survives the install redirect —
  assumed, not verified).
- Production secrets (`AUTH_JWT_SECRET`, `AUTH_ENCRYPTION_KEYS`,
  `GITHUB_APP_PRIVATE_KEY`, `OPENROUTER_API_KEY`) are a config surface, not
  provisioned.
- **The 5 catalogued models are UNVERIFIED against a live key** — run
  `scripts/verify_models.py` before trusting the picker; a model
  that fails the MODE-tag check must be replaced, not shipped.
- Validate the 0.35 gate and 0.72 reuse threshold against production
  `rag.query_signals` logs rather than hand-measured examples.
- **Deferred by decision:** structural citations + NLI (cost/latency);
  token-budget context assembly; Postgres RLS; HNSW tuning (both feared
  defects were measured and did *not* reproduce); PDF/DOCX extraction; the
  self-hosted image.
- `render.yaml` pins `region: singapore`, but **region is fixed at service
  creation** — only a new Blueprint deploy applies it; until then the old
  service pays ~250ms/query.

_End of a phase: update §3/§5/§6/§7 — one dense line, not a narrative._
