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
  a plain Python router. No LLM picks the RAG source. One tool round, never a loop.
- **The agent is CHOSEN by measurement, not by the member** (`app/agent/
  routing.py`). The per-source tabs used to *be* the router; now `choose_agent`
  embeds the question once and takes the best cosine per provider in ONE
  grouped query. "No LLM picks the *source*" is kept — the corpus answers
  "which source resembles this?" directly. Chart vs Q&A is the exception:
  `classify_question` selects a registry metric (connector = `metric.provider`)
  or falls through to cosine; keywords are not the router. Precedence: explicit
  `agent` → chart/refuse (`InsightsAgent`) → a named **authorized** repo →
  single embedded source (no probe) → best score above the 0.35 gate → code
  intent → best score anyway → the old workspace/policy default. **A misroute
  costs a REFUSAL, not a wrong answer** — the routed RAG agent still runs its
  own gate and strict prompt; InsightsAgent's numbers come from SQL.
- **GitHub needs the two keyword rules because it embeds nothing** — with no
  chunks it can never win a cosine race and would be permanently unreachable.
  A named repo must beat the probe (a Notion page *about* a repo outscores the
  repo itself); code intent comes last so a code word inside a document
  question cannot hijack it. `_CODE_INTENT` is collision-free on purpose:
  "issue"/"ticket" are Linear's, "page"/"doc" Notion's, "thread" Slack's.
- **Starter chips span EVERY connected source** (`GET /chat/suggestions` with
  no `agent`; `build_combined_suggestions`). One provider's chips read as "this
  box is for Notion" and hid every other source from someone who never asked —
  the empty state is where most people learn what is connected. Interleaved,
  never concatenated: with a cap, concatenation means the last provider never
  appears. Provider-agnostic titles fill in **only** when no document provider
  resolved (rows predating `source_provider` are otherwise invisible), since
  they are a superset and adding both duplicates a doc's chip.
- The probe carries `org_id` **and** `workspace_id` — a routing decision must
  never be informed by content the asker cannot read — and skips
  `needs_reauth` rows. Every failure degrades to the old default; routing never
  fails a question. The `done` SSE event returns `agent` + `routing_reason`,
  and the pill names both, because with no tab a member cannot otherwise tell
  a misroute from a source that genuinely lacked the answer.

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
  Tailwind/UI-kit — plain CSS vars and global classes. **A space member
  opens on Ask, an owner on management** — the space page is invite/connect/
  delete, all disabled for a member. Deliberately not a redirect: that
  would make the people list unreachable and bounce any link back out.

**Activity Scheduler (`app/schedulers/`)** — a member saves free-text intent
+ a cadence (**daily/weekly/monthly** — daily only became honest once syncing
was automatic; before that it re-summarised the same stale index every morning.
`FREQUENCIES`, `_FREQUENCY_INTERVAL` and `_FIRST_WINDOW` **must all agree** or a
cadence is creatable and then silently gets a weekly window); each run fetches that service's activity since the last run,
hands it plus the prompt to an LLM, and emails the result. **Reads live,
embeds nothing** (the `app/githublive/` pattern).
- **All five sources are schedulable, but only GitHub reads live.** Slack,
  Linear, Notion and Drive read what CHANGED from **our own index**
  (`activity.py::fetch_indexed_activity`, keyed on
  `documents.source_last_modified`) — one query answers both "which changed"
  and "what they say", so the four collapse to one function and Notion/Drive
  become schedulable at all despite having no "activity since T" primitive.
  Only honest because syncing is automatic (§ Automatic freshness); before
  that, `source_last_modified` advanced only when a human pressed Update.
  GitHub stays live — it embeds nothing, so there is no index, and it has a
  real `list_commits(since=)`. `SUPPORTED_PROVIDERS` **must equal**
  `_FETCHERS`; Drive's provider string is **`google`**, not `google_drive`.
- **An indexed report describes CURRENT CONTENT, never a diff** — nothing
  stores the previous version, so `prompts._NO_DIFF_RULE` (rule 9, indexed
  providers only) forbids "added/removed/edited/changed". GitHub is excluded:
  change *is* what it has. Every indexed digest also discloses **when the
  source last synced**, including on a quiet run — that is what separates
  "nothing happened" from "nothing was fetched".
- **A missing connection must not look like a quiet week.** Reading the index
  directly would make "this space has no Notion" indistinguishable from "no
  activity", so `_connection_sync_state` still resolves the connection in scope
  and **raises** — which is also what preserves "a space-scoped report never
  falls back to the org connection". Scope is now a WHERE clause, so a missing
  predicate *leaks* rather than fails; `tests/test_scheduler_indexed.py` pins
  it in both directions.
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

**Automatic freshness (`app/jobs/autosync.py`, `app/llm/pacing.py`)** — nothing
in this codebase ever ingested unless a human pressed Check → Update, which
made staleness a *user chore*. Two columns on `oauth_connections` carry the
only two reasons to sync: `sync_requested_at` (a service TOLD us — stamped by
a webhook handler, never by the sync module) and `last_sync_at` (the interval
elapsed).
- **The poll is the FLOOR, not the plan.** Slack/Linear/Notion can push;
  **Drive can never** — Google requires the push receiver's domain to be
  verified in Cloud Console, which `*.onrender.com` cannot be. A webhook
  delivered while the free instance was cold-started is also simply lost. The
  interval turns both into a delay instead of a permanent hole.
- **`sync_requested_at` is a FLAG, not a queue** — a busy channel stamps it per
  message and the tick reads-and-clears it, so fifty messages produce ONE job.
  That read-and-clear IS the debounce; there is no timer and no counter. A
  webhook handler must therefore never ingest inline (Slack wants a 3s ack).
- **`last_sync_at` is stamped on ATTEMPT, not success** — deliberately. One
  failed sync costs one interval of freshness, which is visible; a hot retry
  loop against a provider's rate limit is not. `needs_reauth` rows are skipped
  entirely: a dead token cannot be fixed by retrying it.
- **`POST /internal/tick` exists because the free instance sleeps.** Render
  free spins down after ~15 min with no *inbound* HTTP — process activity does
  not count, so every in-process loop stops shortly after the last user leaves
  and "syncs every 6 hours" becomes fiction. `.github/workflows/tick.yml`
  drives it (in-repo, so deploying starts it; free and unlimited on a public
  repo). The workflow **GET `/health` until 200, then POST `/internal/tick`**
  — a 503 from Render's proxy during cold start used to fail the job in ~3s
  (`--retry 2`) and never wake the box. An unset `INTERNAL_TICK_SECRET`
  **404s the route** — an unauthenticated tick is a free way to spend every
  tenant's provider quota.
- **Background LLM work reserves headroom for people** (`pacing.py`).
  `build_aux_llm_provider` shares the main key/endpoint *by default*, so ingest
  contextualization and a live question compete for ONE 15 rpm limit, and a 429
  on the answer path is a *failed* answer. `LLM_RESERVE_RPM=5` of
  `LLM_MAX_RPM=15` is never spendable by background work — a reservation, not a
  prediction. **Interactive calls are never throttled**; they only record
  themselves, hooked into `log_llm_call` (the one function both call sites
  already route through, so no future call site can forget). A refused slot
  returns the bare chunk *without* spending a request.
- **`LLM_AUX_BASE_URL` + `LLM_AUX_API_KEY` is the STRUCTURAL fix and is
  preferred** — separate endpoints cannot contend, so `pacing` skips its gate
  entirely and background work stops paying quality (un-prefixed chunks) to
  protect a limit it never touches. **BOTH or neither**: a foreign `base_url`
  with the main key 401s on every contextualization and degrades *silently*, so
  half-configured means "not configured" (`aux_has_own_endpoint`). It changes
  quota, **not policy** — contextualization sends tenant chunk text, so an
  endpoint that trains on prompts is no more acceptable here than on the answer
  path.

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

**Visual Representation (`app/insights/`, Ask chat)** — charts are asked for in
the same box as grounded Q&A. There is no Visualizations tab; `/visualizations`
redirects to `/chat`. Scope is the conversation's: company Ask reads org-wide
facts, a space's Ask reads that space only — no separate company dashboard.
- **Chart vs Q&A is classified, not regexed.** `classify_question`
  (`app/insights/resolve.py`) returns `qa` | `chart` | `refuse` against the
  registry. Chat uses `fail_open=True` so a dead/unparseable classifier is a
  document question, never a blocked leave-policy; `POST /insights/ask` uses
  `fail_open=False` and still refuses. Connector is `metric.provider`, not
  cosine over chunks. Requested shape is validated (`pie` needs `group_by`;
  `line`+group becomes `bar`; sentiment stays `diverging_bar`).
- **`InsightsAgent` is a LangGraph node like GitHub** — no RAG; SQL via
  `store.run_metric`. `choose_agent` routes it *before* the cosine probe. A
  visual we cannot count still goes here so retrieval cannot invent a number.
  An explicit pie/bar/line of something we don't store is a refusal that names
  what IS countable and tells them to re-ask without a chart for the file.
- **The invariant: numbers come from SQL over `activity_facts`; the LLM never
  produces a number, an axis or a date.** A prose answer that is wrong hedges
  and cites; a bar chart that is wrong reads as a *measurement*, and neither
  the gate nor the strict prompt can check arithmetic. So there is **no image
  generation** — a PNG of numbers cannot be filtered, re-scoped or clicked
  through, which is the entire point of the section.
- **`registry.py` is the semantic layer** — 2 hardcoded `Metric`s so far (Notion + Drive), each a
  FIXED aggregate fragment plus whitelisted `dims`. Same discipline as
  `llm/catalog.py`. `tests/test_insights_registry.py` forbids `{`, `%` or `;`
  in any fragment and requires every `DIMENSIONS` value to be a bare
  identifier: `period` and `group_by` are grammatically identifiers, so they
  cannot be bound as `%s` and are spliced — that whitelist is the only thing
  between them and an injection. `PERIODS` is closed at week/month/quarter.
- **A `Metric` is a definition, a `Panel` (`panels.py`) is a VIEW of one.**
  "Top editors" is `docs_changed` grouped by `actor`, not a second metric —
  otherwise every grouping is a duplicate definition to keep in agreement.
  `panels.validate()` (test-time, not import-time) checks each panel against
  the metric it claims.
- **Facts are derived from the index, not from a second API call**
  (`facts.py`, one `INSERT ... SELECT` over `documents`). Idempotent via the
  two PARTIAL unique indexes; an edit **moves** the fact (`DO UPDATE`) because
  a page edited five times is one page. Hooked at `worker.py`'s
  `_record_insight_facts` next to the answer-cache clear, running
  unconditionally on success — which is what makes a *new* ingest self-backfilling.
  Tenants that connected *before* charts shipped still have an empty
  `activity_facts` until `backfill_all_document_facts` runs (on the tick, and
  lazily inside InsightsAgent on an empty chart). Authors stay NULL where they
  were never captured; we do not invent them. Its own `try/except`: a stale
  chart is a stale chart, but failing a finished job turns it into a retry loop.
- **The author is captured at SYNC time, never at view time**
  (`documents.source_last_editor`). Free where the source already tells us —
  Drive's `lastModifyingUser(displayName)` rides along in the `files.list` we
  already make; Notion's `last_edited_by` is an **id**, so one cached
  `GET /users/{id}` per DISTINCT person (the `slack.py::_display_name` trick).
  An unknown editor is `None`, never a placeholder: the page still counts, it
  just leaves the editor breakdown, because crediting the wrong person is worse
  than a gap. **Authors are NOT backfillable** — never captured — so every
  panel reports `first_fact_at` and the UI renders "Measured since <date>";
  without it an axis silently starts on deploy day and reads as if nobody
  worked before.
- **`points: null` means the panel FAILED, `[]` means it ran with nothing to
  show** — and the UI says three different things ("nothing connected",
  "waiting for the first sync", "no activity in this window"). Collapsing
  those is what makes a dashboard untrustworthy. One broken panel reports
  itself rather than blanking the page.
- **`/insights/dashboard` is ONE round trip** for every panel: six panels
  firing six requests is six cold starts on a free instance, and a dashboard
  arriving in pieces reads as broken rather than slow.
- Member-level like `/schedulers` — a chart aggregates rows the asker can
  already retrieve in prose, so `require_admin` would be theatre. A space
  scope goes through `assert_member` and a non-member gets **403, never an
  empty chart**: empty is a claim about content they may not have.
  `scopes.py` is deliberately NOT `_connected_providers` — that one omits
  `last_sync_at`/`needs_reauth` on purpose, and freshness needs exactly those.
- **Freshness renders FIRST**, and `needs_reauth` is separate from an old date:
  auto-sync skips a dead token entirely, so "last synced 6 days ago" invites
  someone to wait for a sync that can never happen.
- **Charts are hand-rolled SVG** (`frontend/components/Chart.tsx`) — no chart
  library. This frontend has no UI kit; a 325MB dependency already cost a
  deploy once. A grouped bar chart renders as a **ranked leaderboard**, a pie
  as shares of the same groups, not one bar per person per bucket. One bucket
  is a dot, not a divide-by-zero.
- **Where each provider's facts are written is NOT arbitrary.** Indexed
  providers derive `doc_changed` from `documents` (`facts.py`). GitHub uses a
  **facts-only sync branch** (`autosync.record_due_facts` →
  `github_facts.py`): it has no ingestion job, so `UNSYNCABLE_PROVIDERS` stays
  true and `FACTS_ONLY_PROVIDERS` names it too — both are right. Linear and
  Forms ride the **ingest job's** success hook, because they *also* ingest: on
  the tick the ingest path's `_stamp_attempted` would already have made the
  connection not-due, so their facts would silently never run. Linear also
  needs the built adapter, which only the job has.
- **GitHub keeps three people apart** — `pr_opened` (author), `pr_merged`
  (`merged_by`), `pr_reviewed` (reviewer), never one "activity by person"
  count: "ada did 12 things" is not a fact anyone asked for. `state="merged"`
  is OURS — GitHub reports a merged PR as *closed*, so counting off its state
  counts abandoned branches. Reviews cost ONE call per PR, so the PR set is
  capped first and only the newest `max_reviewed_pull_requests` are reviewed.
  Commits are the same facts-only path (`commits_by_author`, newest
  `max_commits` per repo) — still no vectors.
- **Linear's `subject` is the TEAM**, so grouping by subject *is* "by team" —
  which is what answers the request this feature came from. Completion is
  decided by `state_type == "completed"` (Linear's own lifecycle category), not
  the state NAME, which breaks when a team renames "Done"; `canceled` is
  terminal but is never a completion.
- **Slack charts come from the index and say so.** Ingest stores *threads* and
  `SLACK_MIN_THREAD_CHARS` drops short ones, so the counts are conversations
  and a FLOOR — every Slack metric's `caveat` states both. `subject` is the
  channel, extracted in SQL from the `#channel: snippet` title (per-provider,
  so a Notion page called "Q3: goals" keeps its whole title), which also means
  a renamed channel corrects itself on the next sync. Attribution rides
  `fetch_document`, **never the listing**: the listing is also change detection
  and made zero `users.info` calls before charts existed.
- **Forms sentiment is the ONE metric whose numbers begin with an LLM**, and it
  is fenced hard. Responses are **never indexed** (`app/sources/google_forms.py`
  is not a `SourceAdapter`) — embedding a survey answer would make "what did
  Ada say about management?" answerable, the opposite of what a survey
  promises. Each response is classified ONCE to one label from a fixed set, on
  the **aux** endpoint, and **the response text is discarded**: only the label,
  the score and the question survive, with no respondent handle at all. A
  failed classification is *missing data*, never "neutral" — neutral would drag
  every chart to the middle and make a broken endpoint look like a calm
  workforce. `DO NOTHING` on conflict, so a label never drifts as models
  change. There is deliberately **no single company sentiment score**.
- **Two structural protections on sentiment**, not conventions:
  `min_group_count=5` suppresses small buckets **in SQL**, and `owners_only`
  keeps it to org admins and space owners. The floor counts the **TOPIC**, not
  each label — a diverging bar splits a topic across five labels, so a plain
  `HAVING count(*)` hid topics with plenty of responses (caught by a test, not
  in review); it is summed with a window function, hence the subquery. A gated
  panel is **omitted**, not returned empty, and the ask box says "can't chart
  that here" rather than "not allowed" — the latter confirms sentiment is being
  collected to exactly the people it is collected on.
- **The Forms OAuth scope is opt-in** (`GOOGLE_FORMS_ENABLED` appends
  `forms.responses.readonly` in `GoogleSettings.from_env`). Not in the default:
  an already-connected tenant does not have it, so defaulting it on would force
  every tenant to reconnect. A 401/403 from the Forms API says **"reconnect"**,
  because that is the overwhelmingly likely cause and "403" sends someone
  hunting a permissions bug that is not there.
- **`Metric.series_by` is a SECOND fixed grouping**, whitelisted through
  `DIMENSIONS` like `dims`. Only for charts that genuinely need two dimensions
  (a diverging bar is topic BY label); fixed per metric rather than requestable.
- **The ask box (`resolve.py`) is a question-to-spec resolver, not a chatbot.**
  The model SELECTS a registry key and nothing else — never SQL, never a
  column, never a number — and everything it returns is validated, so a
  hallucination costs a refusal that names what IS available. Only metrics in
  the caller's scope are offered, because offering an unreachable one invites
  the model to pick it. **Validation is the gate, not the prompt** — a test
  assumes the prompt LOST and asserts the outcome is still a refusal. A missing
  `period` defaults; an unknown one falls back, because `period` is spliced
  into `date_trunc` and this layer must never hand `run_metric` one. A refusal
  is a **200** with `charted: false`: "here is what I can show" is an answer,
  not an error. A follow-up uses `patch_spec`, which cannot change the metric.
- **Pins are personal** (`insight_pins`, scoped `(org_id, user_id)` like
  `schedulers`) and store the SPEC, never the numbers — re-running is one
  `GROUP BY`, and snapshotting would freeze a chart meant to stay current (the
  opposite of `scheduler_reports`, which snapshots *because* a report is a
  record of one moment). Validated against the registry on write: a pin is the
  one place a bad value would PERSIST rather than fail once.

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
              + google_forms.py (live reads, NOT an adapter — never indexed)
app/githublive/ GitHub's whole data path — live reads, no vectors
app/agent/    Agent + per-source agents + orchestration (LangGraph) + routing
app/security/ crypto, untrusted (scrub), rate_limit, client_ip
app/auth/     OAuth providers, credentials, users, magic_link, session, email
app/jobs/     ingestion queue + worker + scheduler_queue + autosync
app/llm/      + pacing.py (rate-limit headroom for interactive calls)
app/insights/  registry + panels + store (SQL) + facts + github_facts +
              linear_facts + sentiment + scopes + resolve (ask box) + pins
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
- **The LLM rate-limit window is process-global state, and tests feed it.**
  `log_llm_call` counts every call including a fake one, so a test late in a
  run saw a window full of other tests' calls, got its background slot refused,
  and looked like a broken retry — three `test_ingest_progress` cases passed
  alone and failed in a full run. `conftest` resets it autouse; the leak is
  invisible in isolation, which is why it resets by default.
- **~6 LLM calls per question** (rewrite, decompose, generate, audit,
  web-decision, tone) means **~1.6 questions/min saturates the background
  budget**, so under sustained chat load contextualization degrades rather than
  waits. Visible only via `pacing`'s `logger.info` — and the fix is
  `LLM_AUX_BASE_URL`/`LLM_AUX_API_KEY`, not a bigger reserve. Note Gemini limits
  are **per project**, so a second key in the same project shares the same
  15 rpm; a separate aux endpoint means a different project or vendor.
- **Do not name the routing wrapper class in `build_aux_llm_provider`'s
  docstring** — `test_model_selection` asserts its absence by reading the
  function's *source*, so even a prose mention fails the test. Cost: one
  confusing red run.
- The grounded prompt is ~2.3k tokens, 96% fixed prefix, already ordered for
  provider caching. **Never move CONTEXT/QUESTION earlier.**

**Charts**
- **"Show a pie of files" is not "pie chart".** `_asked_for_a_plot` used to
  require the word "chart" after pie/bar/line, so a vague plot ask fail_opened
  to RAG and the member saw "I don't know". Bare `pie`, plus `_fallback_spec`
  (match the registry *label* in the question) recover a spec when the model
  says qa; two equally good matches still refuse.
- **A pie of NULL actors is one unnamed filled circle.** `Chart.tsx` treated
  `series[0] === ""` as ungrouped and sliced by month; the Insights pill used
  `agent` as identity, and `"insights"` was missing from LABELS, so a real
  Drive pie said "No answer found". Pass `groupBy`; empty names render
  "Unknown"; the pill names the connector + "Charts".
- **`python -m app.db.migrate` does NOTHING** — `migrate.py` has no `__main__`
  block, so it imports the module and exits silently. Call `apply_schema()`.
  Cost: one "why is the table missing" detour.
- **A fresh local Postgres makes the suite fast.** `brew install pgvector` +
  `brew services start postgresql@17`, then `createdb handbook_dev` and
  `apply_schema()`; `DATABASE_URL=postgresql://localhost/handbook_dev pytest …`
  wins because `conftest`'s `load_dotenv()` does not override the environment.
  The insights suite is 0.1s local vs minutes against remote Supabase.
- **`doc_staleness` and `corpus_size` are deliberately NOT registry metrics.**
  Staleness needs the LATEST fact per document (a page edited five times is one
  page) — a `DISTINCT ON`, not a `GROUP BY` over a window — and `corpus_size`
  counts `documents`, not `activity_facts`. Forcing either into `run_metric`
  makes one function mean two things.
- **A `documents` INSERT needs `external_workspace_id`** (NOT NULL) — test
  fixtures inserting `oauth_connections` rows directly must supply it.
- `invite_member` returns a **`User`**, not an id; `create_workspace` wants
  `.id`. Passing the object raises "cannot adapt type 'User'".
- **A suppression floor over TWO dimensions is not `HAVING count(*)`.** A
  diverging bar splits each topic across five sentiment labels, so a per-row
  floor hid topics that had plenty of responses. It must sum across the series
  (`sum(value) OVER (PARTITION BY bucket, "group")`), and a window cannot
  appear in HAVING — hence `store._floored`'s subquery. `"group"` is a
  reserved word and must be quoted.
- **Adding an author to a LISTING is not free even when the name is cached.**
  Slack's `list_documents` is also the change-detection path and made zero
  `users.info` calls; attributing there added one per distinct author to every
  "Check". `fetch_document` already resolves the name, and the pipeline prefers
  `doc.last_editor`, so the attribution lands for nothing. An existing Slack
  test caught it by rejecting the unexpected call.
- **`panels.validate()` catches a panel naming a metric that does not exist.**
  It caught `pr_authors`/`pr_mergers` being written as metrics when they are
  `prs_opened`/`prs_merged` grouped by `actor`. Keep panels and metrics
  distinct or every grouping becomes a duplicate definition.
- **A scrubbed-to-empty survey response is dropped, not classified.** That is
  `untrusted.py` failing closed, and it means the response most certainly an
  attack produces no data point. Correct, and worth knowing before diagnosing
  "why is this response missing".

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
partial unique indexes: org-wide vs workspace; `sync_requested_at` webhook flag
+ `last_sync_at` poll floor, see §3 Automatic freshness) · `ingestion_jobs`
(+`phase`/`attempts`/`progress_at`) · `magic_link_tokens` · `oauth_states` ·
`github_install_pending` · `query_answer_cache` · `api_rate_counters` ·
`workspaces` / `workspace_members` · `org_signup_requests` · `schedulers`
(scoped by `org_id` **and** `user_id`, unlike every other tenant table; `model` NULL = the configured default) ·
`activity_facts` (the ONLY numeric substrate for charts; two partial unique
indexes on `external_id`, org-wide vs workspace) · `insight_pins` (personal,
`(org_id, user_id)`; stores the spec, never the numbers) · `scheduler_reports` (same `(org_id, user_id)` scoping; snapshots its labels
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
per-source agents + LangGraph routing (including `InsightsAgent`); golden-set eval in CI (+ nightly
RAGAS); identity/OAuth/admin/ingestion queue/HTTP API/streaming chat; Next.js
portal; Workspace-within-a-Workspace; signup-approval queue; injection,
latency, security and eval hardening; the Activity Scheduler; Multi-Model
Selection (OpenRouter, ~5 models, per-request routing); automatic freshness (interval + webhook-flag sync, external tick, LLM pacing);
Visual Representation, **all five phases** — `activity_facts`, metric registry
+ panels, charts **in Ask** (no Visualizations tab; `/visualizations` redirects
to `/chat`; `InsightsAgent` + `classify_question` rather than a keyword regex), editor capture at sync time, GitHub PR/merge/review facts on a
facts-only sync branch (PRs plus commits), Linear completion-by-team on the ingest job, Slack
conversations from the index, the constrained resolver + personal pins API, and
Forms sentiment (never indexed, owners-only, 5-response floor). Indexed
tenants that predate charts get `activity_facts` from `backfill_all_document_facts`
(tick + lazy on an empty Ask chart). "Show a pie of files…" recovers a spec
when the model says qa.

**Pending / known gaps**
- Charts: **the Google Forms path has never run against a real form.** The
  Forms API calls, the `mimeType` listing and the scope behaviour are written
  from the documented shapes and tested against a fake reader only. Enabling
  `GOOGLE_FORMS_ENABLED` also requires every tenant to reconnect Google, so
  this is the one part of the feature that must be walked through live before
  it is trusted. **Do not chart a Sheet by embedding it** — numbers from
  retrieved chunk text are unfalsifiable; Drive still skips
  `application/vnd.google-apps.spreadsheet`. A form export in a connected
  folder is Q&A fodder only if we add that MIME later, never a pie. Plan:
  `docs/plans/2026-09-02-visual-representation.md`.
- Charts: **no frontend test infrastructure** — `Chart.tsx` (including the
  diverging bar) and inline Ask charts are covered by `tsc --noEmit` only,
  never a rendered assertion. Do not add a React test stack as a side effect
  of a chart.
- Charts: **no browser click-through yet** — the org-member vs space-member
  difference and the sentiment gate are asserted at the API, not in a real
  page load.
- Charts still deferred, each with the reason in the code: `doc_staleness` and
  `open_pr_age` (need `DISTINCT ON`, a different query shape from
  `run_metric`); the PR **cycle-time breakdown** (coding → waiting → in review
  → merge, the highest-value engineering chart, needs review timestamps *and*
  first-commit dates: two more calls per PR); Slack `active_hours` (needs a
  day×hour heatmap `Chart.tsx` cannot draw) and `thread_response_time` (the
  index stores a thread, not its replies).
- Charts: **`first_fact_at` starts on deploy day for authorship.** Counts
  backfill from `source_last_modified`; author names cannot — never captured.
  The UI says "Measured since <date>", which is the honest floor, not a fix.
- Scheduler: **email delivery is unverified — `console` only** (a failed send
  now costs only the notification: the report is stored and readable in-app
  either way).
- Indexed reports inherit the ingest pipeline's filters and shape: content
  dropped by `SLACK_MIN_THREAD_CHARS` can never appear in a report, Slack items
  are threads (not per-message, so no author attribution), and Notion/Drive/
  Linear chunk text carries its LLM context prefix — factual but verbose, and
  it spends the char budget.
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
- **Auto-sync is polling ONLY so far** — `request_sync()` and the flag column
  exist, but **no webhook endpoint calls them yet**, so today's worst case is
  the 6h interval rather than one tick. Slack/Linear/Notion handlers are the
  next step; Drive can never have one.
- The **Check button is still in the UI** on purpose: it is the manual override
  until an unattended sync is observed working in prod. Remove it only after
  that.
- Auto-sync needs THREE things outside the repo: the migration, Render's
  `INTERNAL_TICK_SECRET`, and GitHub repo secrets `TICK_URL`/`TICK_SECRET`.
  Missing the last two means the schedule runs and calls nothing (exit 0 by
  design, so a fork does not fail CI).
- **GitHub disables scheduled workflows after 60 days of repo inactivity** — a
  dormant repo silently stops ticking, and every freshness guarantee stops with
  it. cron-job.org against the same endpoint is the punctual alternative.
- Render free gives **750 instance-hours/month**; an always-warm service is
  ~730, so this design consumes essentially the whole allowance for one
  service.
- **Deferred by decision:** structural citations + NLI (cost/latency);
  token-budget context assembly; Postgres RLS; HNSW tuning (both feared
  defects were measured and did *not* reproduce); PDF/DOCX extraction; the
  self-hosted image.
- `render.yaml` pins `region: singapore`, but **region is fixed at service
  creation** — only a new Blueprint deploy applies it; until then the old
  service pays ~250ms/query.

_End of a phase: update §3/§5/§6/§7 — one dense line, not a narrative._
