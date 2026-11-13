# ARCHITECTURE.md — Complete Technical Reference

> **Purpose of this file.** A single, end-to-end description of what this project
> is, how every component works, and how data flows through it — enough for a
> human or an AI agent to gain full context without reading the whole codebase
> first. It complements two sibling docs:
> - **CLAUDE.md** — the constraints. Feature status is `docs/handbook/`.
> - **README.md** — the quickstart.
>
> This file is the *system reference*. When code and this file disagree, the code wins — keep this updated.
> **Product status (what is live, tested, blocked, in progress): [PRODUCT_STATUS.md](docs/handbook/PRODUCT_STATUS.md).**
> A walk through each feature: [current-features.md](docs/handbook/current-features.md).
> *Last updated: 13 November 2026.* §5–§6 and §8–§11 describe the core write and read paths;
> Agent mode is §6.5, limits §6.4; later capabilities are summarised in §7 and §15.

---

## 1. What this system is

A **multi-tenant Retrieval-Augmented Generation (RAG) platform for company Q&A over connected tools.**

- **Tenants (organizations)** connect **Notion, Google Drive, Slack, Linear and GitHub** (GitHub is read live, never indexed).
- Their **employees ask natural-language questions** — on the web or in Slack — and get answers **grounded in that organization's own content**, limited to what each person may open in the source tool. One cited document is named under the answer. Two or more keep a small number on the sentence. The link is the address stored when that page was indexed.
- **Strict tenant isolation:** one organization can never see another's content.
- **Two offerings:** Handbook Cloud (we run it) and **Self-Hosted** (one company per server, from versioned Docker images and the compose package in `deploy/selfhost/`). The design favors components that run **locally, with no external paid dependency** (local embeddings, local reranker, keyless web search, a swappable LLM endpoint).

**Why RAG, not fine-tuning:** policies are *facts that change* (leave rules, reimbursement limits). RAG retrieves the current document text at question time, so updating a policy is just re-ingesting a file — no retraining. The answer then names the page it used, and the link is the one stored at sync.

---

## 2. Core design principles

1. **Everything is a swappable interface + factory.** Each capability has `base.py` (abstract contract) + one or more concrete impls + `factory.py` (`build_*()` reads config, returns the impl). The rest of the app depends on the *interface*, never a concrete class.
2. **Orchestrators have no `base.py`.** A package that only *composes* existing interfaces (e.g. `app/rag/`, `app/ingestion/`) skips the abstract contract — there's nothing to swap. It still keeps `pipeline.py` + `factory.py` for consistency. Exception: `app/agent/` *does* get a `base.py` because it has many backends (one agent per source, GitHub, Insights).
3. **Config lives in exactly one place.** `app/config/settings.py` — frozen dataclasses with `from_env()`. Nothing else calls `os.getenv` for config.
4. **All failures raise `ProviderError`** (or a subclass in `app/core/exceptions.py`), carrying the original via `cause=` / `raise ... from`.
5. **Tenant isolation is enforced by the query, not the index.** Every tenant-scoped read/write requires an `org_id`; retrieval filters `WHERE org_id = …` *before* ranking.
6. **Grounding is enforced by two independent layers** (a cheap confidence gate + a strict prompt), never one.
7. **Dependency-light.** Thin official SDKs over frameworks (plain `openai` client not LiteLLM; `notion-client` not `llama-index`). Presentation-only deps (`rich`) are confined to `scripts/` and never imported by `app/`.

---

## 3. Tech stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | `from __future__ import annotations`, type hints throughout |
| LLM | Any **OpenAI-compatible** endpoint via the `openai` client | `LLM_ADAPTER` picks a provider from a fixed list and supplies its base URL; `LLM_MODEL` is only a parameter. `custom` is the one case that reads `LLM_BASE_URL`. Checked at boot. |
| Model choice | Claude, per request | The picker offers Claude Sonnet 5.5 and Claude Haiku 5.5 (`app/llm/catalog.py`), on `CLAUDE_API_KEY`; the default is the deployment's `LLM_MODEL`. Admins can add one company model (provider presets, fixed hosts). Background work (context lines, figures, gap grouping) runs on the aux model (`LLM_AUX_*`), never pickable and never charged. |
| Embeddings | **BGE-M3** (1024-dim) | `local` (sentence-transformers) or `remote` behind the same interface. Deploy images use the remote backend when RAM is tight. |
| Vector DB | **Postgres + pgvector** (0.8+) | `halfvec(1024)` with an HNSW cosine index; `chunks` partitioned per organization with row-level security; connection pooling. Also holds jobs, graph, facts, memory. |
| Reranker | **`BAAI/bge-reranker-v2-m3`** | Local cross-encoder, or a remote backend. Default candidate pool is **40**, then `RAG_TOP_K` (5). |
| Rate limits | Token buckets (`app/ratelimit/`) | In Redis (Valkey, `volatile-lru`) when `REDIS_URL` is set, else per process. Redis holds rate limits only. |
| Sources | Notion, Google Drive, Slack, Linear (indexed); GitHub (live, never embedded); Google Forms (sentiment labels only) | One `SourceAdapter` per indexed source. GitHub and Forms are not adapters. |
| Access | Document sharing captured at sync | Drive per file, Slack per private channel, Linear per private team, GitHub per private repo. Notion is per space. One SQL predicate (`security/visibility.py`). |
| Web search | **DuckDuckGo** via `ddgs` (keyless) | Tavily is the documented production swap |
| Files | Cloudinary, or Postgres | Chat uploads: original bytes and extracted text as private authenticated assets; with no Cloudinary keys the text is kept in Postgres |
| Email | SMTP, Resend or SendGrid | `EMAIL_SENDER=console` prints links locally and is not a production sender |
| Safety | Prompt policy, scrubber, link provenance, canary, safeguard model, identifier masking | `app/security/`, `app/guard/`, `app/privacy/` |
| Writes into tools | MCP (Model Context Protocol) | Agent mode only, through servers an admin adds; Slack and Google through Handbook's own MCP servers (`app/mcphost/`) |
| Frontend | Next.js 15, plain CSS | Session only in the httpOnly cookie |
| Tests/eval | `pytest` + optional **RAGAS** (`[eval]` extra) | |

---

## 4. High-level architecture

```mermaid
flowchart TB
    subgraph Edges["Entry points"]
        WEB["Next.js portal → FastAPI /chat (streaming)"]
        SLK["Slack bot — /slack/events"]
        HOOK["Webhooks — /webhooks/{notion,linear,google}"]
        TICK["External tick — /internal/tick"]
        CLI["scripts/cli.py"]
        ACT["Agent mode — /actions, /slack/interactivity"]
    end

    subgraph Agents["app/agent/ — routing + LangGraph"]
        ROUTE["choose_agent (cosine probe + classifier)"]
        SRCA["per-source RAG agents"]
        GHA["GitHubAgent (live tools)"]
        INS["InsightsAgent (charts, SQL)"]
    end

    subgraph RAG["app/rag/ — the read path"]
        PIPE["RagPipeline: rewrite → retrieve → gate → generate → audit"]
        GRAPH["app/graph — knowledge graph plan + walk"]
        LIVE["app/livetools — live re-reads"]
        PMEM["app/memory/personal — personal memory"]
    end

    subgraph Write["Write path"]
        JOBS["app/jobs — queue, worker, autosync"]
        IPIPE["app/ingestion — chunk, contextualize, embed"]
        SRC["app/sources — Notion, Drive, Slack, Linear adapters (+ ACL capture)"]
    end

    subgraph Writes["app/actions — Agent mode"]
        LOOP["tool loop, paused at each write"]
        MCP["MCP servers (presets, admin-added, app/mcphost)"]
    end

    subgraph Data["Postgres + pgvector"]
        DB[("documents (+doc_viewers), chunks, activity_facts,\nkg_* graph, conversations, user_memory, ...")]
    end

    WEB & SLK & CLI --> ROUTE --> SRCA & GHA & INS
    SRCA --> PIPE --> GRAPH & LIVE & PMEM
    PIPE --> DB
    INS --> DB
    HOOK & TICK --> JOBS --> IPIPE --> SRC
    IPIPE --> DB
    ACT --> LOOP --> MCP
    LOOP --> SRCA
```

There are **two end-to-end flows**: the **write path** (ingestion) and the **read path** (query). Everything else is a capability those two paths compose. Agent mode (§6.5) is a third, separate path that writes into the connected tools; it reads company content only through the read path's own retrieval.

---

## 5. END-TO-END FLOW A — Ingestion (the write path)

**Entrypoints:** the Sources page (OAuth connect, then folder or channel scope), webhooks (`app/api/webhooks.py`, Slack events), and the external tick (`POST /internal/tick`). `scripts/ingest_notion.py` remains a one-off CLI. The worker is `app/jobs/`.

**Orchestrator:** `app/ingestion/pipeline.py`, always for one explicit `provider`. A Google sync must not delete Notion documents.

1. **Credential.** The live token comes from the org's `oauth_connections` row (encrypted). A per-org Notion token never falls back to another org's. `needs_reauth` rows are not retried. Google and Slack use the company's own app (`auth/org_apps.py`, `org_connector_apps`), else `.env`'s; saving an app flags that provider's connections for reconnect.
2. **When it runs.** The first ingest is queued on connect (Drive and Slack wait until a folder or channel list is saved). After that, a webhook flags the connection and a sync starts unless one ran in the last 3 minutes; an hourly poll is the floor for anything a push missed. `last_sync_at` is stamped on attempt.
3. **List, then fetch what changed.** `list_documents()` is also where sharing is captured (`DocAccess` on the ref). Unchanged documents are not re-fetched. A listing that looks like it deleted most of the corpus is refused.
4. **For each changed document:** the adapter renders plain text (a Sheet, Excel or CSV file embeds a description of its columns, never its figures); text is chunked at **256 tokens / 40 overlap** with a **4,000-character** ceiling; chunks are embedded (1024-dim, stored as `halfvec`) and written to the organization's own `chunks` partition with `org_id` and, when set, `workspace_id`. A short context line may be prepended at ingest. Empty documents are skipped. Text sent to a remote embedder or model has personal identifiers masked first (`app/privacy`).
5. **Access is stored on the row**, not inferred later: `doc_is_public` and `doc_viewers`. An ACL-capable provider that cannot read sharing indexes the document for the connected account only. An already-indexed document whose sharing goes unreadable keeps its last viewer list.
6. **Document tables, inline.** Each changed document goes through the dataset adapters (`ingestion.pipeline._store_tables`): the table adapter saves tables the document already has (no AI), and the text adapter has the background model list the figures written in its sentences, which code turns into tables (see §6.3). Rows go to `doc_tables`/`doc_table_rows` with the document's access. `documents.tables_checked_at` marks what was read; each sync backfills a bounded batch of unchecked documents.
7. **After a successful job:** activity facts for charts and knowledge-graph capture. GitHub does not ingest; its charts come from a separate facts sync, refreshed when a chart asks.

Contextual retrieval spends LLM calls, so background work leaves headroom for live questions (`app/llm/pacing.py`). `INGEST_CONTEXTUAL_ENABLED=false` stores bare chunks.

---

## 6. END-TO-END FLOW B — Query (the read path)

**Entrypoints:** `POST` chat (web), the Slack bot, and `scripts/cli.py`.
**Router:** `app/agent/routing.py::choose_agent`, then a LangGraph node. The corpus (cosine over each connected tool) picks a document agent. A Chart-mode question goes to `InsightsAgent` (SQL over stored rows, §6.3). A code question, when GitHub is connected, goes to `GitHubAgent` (live tools, nothing embedded). `PolicyAgent` is a legacy fallback, not the product path.

`org_id` comes from the signed session. A space id is paired with it. The asker's email (and prior emails) becomes the `Viewer` that retrieval filters on. Before any work, the question takes the person's and the company's rate-limit buckets and the AI limits for the picked model (§6.4), then a place among the answers being written at once.

### 6.1 Sequence

```mermaid
flowchart TD
    Q["question in a scope"] --> ROUTE["choose_agent"]
    ROUTE --> CHART["InsightsAgent: SQL chart"]
    ROUTE --> GH["GitHubAgent: one live tool round"]
    ROUTE --> RAG["per-source RagPipeline"]
    RAG --> RW["rewrite follow-up if needed"]
    RW --> EMB["embed once"]
    EMB --> REUSE{"reuse previous chunks ≥ 0.72?"}
    REUSE -- yes --> GATE
    REUSE -- no --> RETRIEVE["vector + keyword, RRF, rerank\nviewer filter in the SQL"]
    RETRIEVE --> GATE{"best cosine ≥ 0.35?"}
    GATE -- no --> REC["at most one recovery, then web or refusal"]
    GATE -- yes --> GEN["strict grounded prompt"]
    GEN --> LIVE["optional live re-read of a hit"]
    LIVE --> ANS["answer, or fixed refusal"]
```

### 6.2 Steps that always hold

1. **Rewrite** only when the chat has history. A first question can also be rewritten from a personal-memory *context* fact (office, team). Preferences never rewrite the search.
2. **Reuse** at cosine **0.72** skips retrieval and still passes the gate.
3. **Retrieval** is hybrid: vector plus keyword, fused with RRF (k=60), reranked from a pool of **40** (`RETRIEVAL_CANDIDATE_POOL`) down to `RAG_TOP_K` (5). The gate reads the **best cosine**, never an RRF score or a reranker logit. The viewer predicate is in the same `WHERE` as `org_id`, before ranking.
4. **Gate at 0.35.** Below it, at most one recovery expansion, then one labelled web search for a real external entity, then the fixed refusal. A document the asker cannot see can replace "I don't know" with an access notice that names the connector, never the title.
5. **Generation** uses three modes only: explicitly supported, related but not explicit, or no supporting evidence. Outside text is fenced. Links in the answer must have appeared in that text. A `[n]` marker is kept only when a retrieved chunk sat at that block; the link is `documents.source_uri` from sync. One cited document is named under the answer; two or more keep a superscript. The citation list is stored on the turn and shown again when that chat is opened.
6. **Live read**, when enabled, refreshes at most two hits after the gate, for a question the classifier marks as about current state. A deleted or forbidden item is withheld.
7. **A graph plan** can add a second tool's documents to the same answer. It reuses the walk; it does not start a second agent.
8. **The conversation is personal** (`conversations.user_id`). Turns, a running summary, and last-retrieval chunks are stored for the next turn; a chat can be renamed and pinned. There is no answer cache: every question is answered fresh. An ungrounded answer is logged as a documentation gap.
9. **Sheet rows.** A question that types a cell of a stored table reads that row as a passage (`doctables.store.rows_naming`, through the cell index). With `DOCTABLES_ANSWERS_ENABLED` (off by default) a question the check flags as a figure over a sheet's rows is worked out by `insights/table_answers.py`: the model fills a form, code checks every name and value against the real table, and SQL computes it.
10. **Deep analysis** (chat `mode: "deep"`) reads the top documents in depth; **Extra effort** (`"deep_extra"`) also reads wide across many documents (`RAG_DEEP_*`).

GitHub answers are composed only from tool output (README, commit, commits, pull requests, reviews, branches). No tool call, a bad argument, or a failure returns the fixed fallback. Private repositories are readable only when the asker's linked GitHub login can open them.

### 6.3 Charts

Charts are built only in Chart mode (the web chat sends `mode: "chart"`; in Slack, a question the classifier reads as a chart ask). **The model never emits a number, an axis or a date.**

1. **Spec.** The model fills in a spec (`app/insights/resolve.py`): what to count, splits, filters, measure, period, range and shape, quoting the asker's words for each. Code keeps a part only when its quote is in the question or its value has real rows.
2. **Source of numbers.** Either an activity metric (`activity_facts`: 12 registry metrics plus simple fields discovered per scope, `insights/attr_catalog.py`) or a document table (`doc_tables`/`doc_table_rows`, ranked by document similarity and by matching the question against each table's own name and columns, `insights/tables.py`). A page the question names wins. Uploaded files' tables are reachable only through `doctables.store.UploadScope` (that chat, that uploader).
3. **Validate twice, then SQL.** The resolver and the store both check the spec; only our own identifiers are spliced into SQL and every value is bound. A spec that fails is refused with the reason, never repaired. The viewer predicate applies to every table and fact.
4. **Nothing stored?** Chart mode reads the named page live, then the sentences of at most two matching pages, before refusing.
5. **Draw.** The panel (points, title with measure and unit, an `explain` line) goes to the web, drawn by ECharts (`frontend/components/charts/toOption.ts`), and is stored on the turn as drawn. Slack gets a matplotlib PNG (`app/chartrender/`) and an "Open interactive chart" link.

Two tables can be compared ("target vs achieved", `ChartSpec.versus`), and a workbook is read tab by tab, tabs with the same header kept as one table (`doctables.extract.workbook_tables`).

**Paragraph figures** (`app/doctables/text_adapter.py`). A page with at least three non-year numbers is read in sections (up to 6). The model lists facts `{measure, unit, period, items, value, quote}`; code builds one table per measure, unit, breakdown and period grain (`facts_to_tables`) and keeps a row only when its quote is in the document, every cell is in the quote, and the number binds to its own label. No word lists decide what is read. A damaged reply keeps its complete facts; an unreadable one is retried by later syncs (`MAX_UNREADABLE_READS`) and shown on the admin Feedback page.

### 6.4 Limits

- **Rate limits** (`app/security/rate_limit.py`, buckets in `app/ratelimit/`): every router a person calls takes a request bucket; a question takes the person's and the company's bucket together; sending an email takes its own. A limiter fault lets the request through.
- **Answers at once** (`take_place`): `CHAT_MAX_CONCURRENT` (20) across servers, `CHAT_MAX_CONCURRENT_PER_PERSON` (2). A person over their own cap is refused at once; the shared cap waits up to `CHAT_QUEUE_WAIT_SECONDS`.
- **AI limits** (`app/security/ai_spend.py`): tokens a minute per answering model, input and output apart, for the person and the company (`AI_TOKEN_LIMITS`), and dollars a day in one wallet (`AI_PERSON_DOLLARS_PER_DAY`, `AI_COMPANY_DOLLARS_PER_DAY`, priced by `AI_PRICES`). Every paid call is charged where it is made; a model with no price is not charged.

### 6.5 Agent mode (writes into tools)

`app/actions/` and its own routes; Ask, Chart, sync and the Slack bot import nothing from it.

1. **Off until allowed.** A company admin turns Agent mode on (`policy.act_on`, stored in `action_policies`); every tool starts off and is set to off, read (a lookup while drafting) or write (a card). A space's owner can narrow it for the space.
2. **MCP only.** Every write goes through an MCP server an admin added. Linear, Notion and GitHub are presets pointing at the vendors' servers; Slack and Google are Handbook's own servers (`app/mcphost/`, `POST /mcp/<name>`) over the vendors' regular APIs, with the person's token forwarded. Each person signs in to each server with their own account (`user_connections`), checked to be the company's.
3. **One tool loop, paused at each write** (`loop._agent_loop`, `act_runs`). Company content is read only through Ask's own retrieval for words quoted from the request. At most `loop.MAX_ACTIONS` (5) cards per request.
4. **Approve, then run.** A write runs only from `confirm`, by the person who proposed it, before `expires_at`, with the stored payload (`store.claim`). Private content never goes somewhere public; a wider or unknown audience needs a tick. Every outcome is recorded in `actions`.
5. **Slack.** `agent …` (or `act …`) in a DM drafts as the person; in a channel it drafts from that channel only, and the card is shown only to the asker.

---

## 7. Component reference (`app/`)

| Package | Contract (`base.py`) | Concrete impl(s) | Responsibility |
|---|---|---|---|
| `config/` | — | `settings.py` | Typed frozen dataclasses w/ `from_env()`. **Only** place reading env for config. |
| `core/` | — | `exceptions.py` | `ProviderError` hierarchy (`LLMProviderError`, `EmbeddingError`, `ConfigurationError`, `SourceError`, `WebSearchError`, `DatabaseError`). |
| `llm/` | `LLMProvider` | `OpenAICompatProvider`, `routed.py`, `masking.py` | `generate(prompt)` and optional `generate_with_tools(messages, tools, tool_choice)`. `adapters.py` is the fixed provider list, `catalog.py` the picker, `org_model.py` a company's own model. Both factories return a `MaskingLLMProvider`. |
| `privacy/` | `base.py` | `patterns.py`, `vault.py` | Aadhaar, Virtual ID, PAN, card, mobile, IFSC and UPI values replaced with placeholders before text leaves, restored in the reply. |
| `embeddings/` | `EmbeddingProvider` | `local.py` (sentence-transformers), `remote.py` (HTTP) | `embed(list[str]) -> list[list[float]]`. BGE-M3, 1024-dim, L2-normalized. |
| `db/` | — | `connection.py`, `schema.sql`, `migrate.py` | Pooled psycopg connections (`register_vector` in the pool's `configure` hook). `apply_schema()` uses a **direct** connection (migration must not use the pool). `close_pool()` at every process exit. |
| `vectorstore/` | `VectorStore` | `PgVectorStore` | `create_organization`, `add_document`, `query` (vector), `keyword_search` (optional), `list_organizations` (optional). All tenant-scoped reads require `org_id`. |
| `ingestion/` | — (orchestrator) | `pipeline.py`, `preprocessing.py`, `chunking.py`, `contextualize.py` | The write path (§5). |
| `rag/` | — (orchestrator) | `pipeline.py`, `retrieval.py`, `prompts.py`, `cite.py`, `factory.py` | The read path (§6), including inline citations. |
| `reranker/` | `Reranker` | `local.py` (CrossEncoder) | `rerank(query, candidates, top_k)`. `bge-reranker-v2-m3` (~2.2 GB first download, then cached). |
| `memory/` | `ConversationStore` | `pg_store.py`, `personal.py` | Conversation history, running summary, last retrieval, and personal facts (`user_memory`). |
| `websearch/` | `WebSearchProvider` | `duckduckgo.py` | `search(query, max_results, timeout) -> list[SearchResult]`. |
| `agent/` | `Agent` (+ `AgentResponse`, `Citation`) | per-source agents, `github_agent.py`, `insights_agent.py`, `policy_agent.py` (legacy) | One pinned agent per source; `routing.choose_agent` picks one; LangGraph runs it. |
| `sources/` | `SourceAdapter` | `notion.py`, `google_drive.py`, `slack.py`, `linear.py`; `google_groups.py`, `google_forms.py`, `drive_watch.py` | Indexed adapters capture sharing on the listing. Groups expand on read. Forms are labels only. Drive push channels. |
| `githublive/` | `GitHubReader` | `rest.py`, `access.py` | GitHub read live; `access.restrict` narrows to repos the asker's linked login can open. |
| `security/` | — | `visibility.py`, `untrusted.py`, `links.py`, `outbound.py`, `agents.md`, `rate_limit.py`, `ai_spend.py` | The one document-access predicate; untrusted-text policy, scrubbing, link provenance; which rate-limit buckets a request takes; AI limits. |
| `ratelimit/` | `base.py` | `memory.py`, `redis_limiter.py` | Token buckets, in Redis when `REDIS_URL` is set. |
| `guard/` | `InjectionGuard` | `prompt_guard.py`, `safeguard.py`, `moderation.py` | Prompt-injection scoring at ingest, answer moderation. Its own account when `GUARD_API_KEY` and `GUARD_BASE_URL` are set. |
| `graph/` | — | `identities`, `builder`, `walk`, `plan` | Second Brain knowledge graph. |
| `livetools/` | — | `gateway.py` + per-provider readers | Second Brain live connector reads. |
| `insights/` | — | `registry`, `query`, `resolve`, `store`, `tables`, `describe`, `attr_catalog`, `pins`, `table_answers` | Chart specs, validation and SQL over `activity_facts` and document tables; worked figures for Ask. |
| `doctables/` | `DatasetAdapter` | `table_adapter.py`, `text_adapter.py`, `store.py`, `uploads.py` | Tables inside documents and uploads, saved as typed rows for charts. |
| `chartrender/` | `ChartRenderer` | `matplotlib_renderer.py` | PNG charts for Slack. |
| `attachments/` | — | `extract`, `limits`, `store`, `blobstore` | Chat uploads; bytes + extracted text in Cloudinary, or text in Postgres without it. |
| `actions/` | `base.py` | `loop`, `service`, `mcp*`, `policy`, `exposure`, `store` | Agent mode (§6.5). |
| `mcphost/` | `base.py` | `slack.py`, `google.py` | Handbook's own MCP servers over Slack's and Google's regular APIs. |
| `feedback/` | — | `store.py`, `topics.py` | Answer ratings and documentation gaps, grouped into topics. |
| `schedulers/` | — | `store`, `activity`, `runner`, `worker` | Scheduled reports, emailed via `auth/email.py`. |
| `updates/` | — | `check.py`, `announce.py` | The version running, the release feed, and the release email to self-hosted admins. |
| `jobs/` | — | `queue`, `worker`, `autosync` | Durable ingestion queue; interval + push-triggered sync. |
| `auth/` | `OAuthProvider` | per-connector OAuth, `org_apps`, `magic_link`, `session`, `email`, `email_change`, `users` | Sign-in, connector auth, each company's own Google and Slack app, email delivery, email change, the company email domain. |
| `api/` | — | FastAPI routers incl. `slack_events.py`, `slack_act.py`, `webhooks.py`, `mcp_host.py`, `notifications.py`, `updates.py` | The only place `org_id` enters a request (from the session). |

**Other top-level:**
- `evaluation/` — golden-set eval (deterministic path-firing tier + RAGAS tier). Peer to `scripts/`/`tests/`.
- `scripts/` — entrypoints: `cli.py`, `init_db.py`, `run_worker.py`, `create_company.py` (self-hosted install), `ingest_notion.py`, `ingest_linear.py`, the `check_*`/`verify_*` probes, `probe_injection.py`, `release_feed.py`.
- `deploy/selfhost/` — the self-hosted compose package (install, upgrade, backup scripts and guides); `deploy/updater/` — the updater container behind Admin › Updates.
- `tests/` — pytest suite (isolation, partitions, grounding, access, charts, tables, Agent mode, limits, masking, and more).

---

## 8. Database schema (`app/db/schema.sql`)

The full column list lives in `schema.sql` and in CLAUDE.md §6. The groups that matter:

| Group | Tables |
|---|---|
| Tenant | `organizations` (`email_domain`), `users`, `user_email_aliases`, `workspaces`, `workspace_members` |
| Sign-in | `magic_link_tokens`, `oauth_states`, `email_change_requests`, `org_signup_requests` |
| Connectors | `oauth_connections` (encrypted tokens, `source_config`), `org_connector_apps` (each company's own Google and Slack app), `drive_watch_channels`, `github_install_pending` |
| Corpus | `documents` (`doc_is_public`, `doc_viewers`, `source_meta`), `chunks` (`halfvec(1024)`, generated `content_tsv`, injection score; one partition per organization in schema `chunk_partitions`) |
| Ask | `conversations` (personal: `user_id`; `title`, `pinned_at`), `conversation_turns`, `conversation_last_retrieval`, `conversation_attachments` (metadata only), `user_memory` |
| Charts & graph | `activity_facts`, `insight_pins`, `doc_tables`, `doc_table_rows`, `doc_text_queue` (read marker), `kg_entities`, `kg_edges`, `kg_evidence`, `person_identities` |
| Agent mode | `action_policies`, `actions`, `act_runs`, `mcp_servers`, `user_connections` |
| Ops | `ingestion_jobs`, `schedulers`, `scheduler_reports`, `feedback_and_gaps`, `gap_topics`, `live_tool_calls`, `update_notices`, `schema_marks` (one-time data fixes) |

Rate limits are in Redis or process memory (`app/ratelimit/`), never in Postgres.

`chunks.embedding` is `halfvec(1024)` (2 bytes a dimension) to match BGE-M3. Query vectors are bound as halfvec too, or the HNSW index is skipped, and every pooled connection sets `hnsw.iterative_scan = strict_order` because all tenants share one index. Changing the model means changing `EMBEDDING_DIM` and re-ingesting. Deletes cascade from `organizations` and `workspaces`. A workspace id is nullable: `NULL` means org-wide, and a space query never also returns org-wide rows.

---

## 9. Multi-tenancy & isolation (the central invariant)

Four boundaries, all in the query or in a check that runs before it:

1. **Company.** Every tenant table has `org_id`. Retrieval filters `WHERE org_id = …` before ranking (`tests/test_isolation.py`). The id comes from the session cookie in `app/api/deps.py`, never from the client. `chunks` adds a second wall: one partition per organization (made and dropped by triggers on `organizations`) and row-level security, so a pooled connection (role `handbook_app`) sees only the rows of `get_connection(org_id=...)`, and none without it (`tests/test_chunk_partitions.py`).
2. **Space.** `workspace_id` is always paired with `org_id`. A space sees only its own rows.
3. **Document.** `security/visibility.py` is the one spelling of "public in this scope, or shared with this person". It runs on vector search, keyword search, recent chunks, charts, starter chips, and the graph walk. People match by email, including a prior sign-in email.
4. **Person.** Chats, reports, pins, memory, and attachments are keyed by `(org_id, user_id)`. Holding someone else's conversation id does not open their chat.

What the connector token can reach (a Drive folder, a Slack channel list, GitHub's granted repos, pages shared with the Notion integration) is a separate limit from who inside the company may read a row.

---

## 10. Grounding & anti-hallucination (two layers)

Neither layer alone is trusted. A similarity threshold cannot cleanly separate "answerable" from "on-topic but unanswered" on a small sample (CLAUDE.md §5).

1. **Confidence gate (cheap, pre-LLM).** `RAG_SIMILARITY_THRESHOLD` = **0.35** (just above noise ~0.30). Below it → fallback with **no LLM call**. Catches irrelevant-context noise cheaply.
2. **Strict prompt (fine-grained).** `prompts.py` forbids outside knowledge and orders the model to emit the *exact* fallback string when the context doesn't directly answer — even if on-topic. Handles the "related-but-doesn't-answer" case the threshold can't.

The **fixed fallback string** lives in ONE place (`RagSettings.fallback_response`) and is consumed in three that must agree: the gate, the prompt's refusal instruction, and `_is_refusal()` detection.

---

## 11. Conversation memory, incremental summarization & retrieval reuse

- **Verbatim window:** the most recent `MEMORY_RECENT_TURNS` (=3) turns are kept in full.
- **Incremental summarization (Phase 8):** after *every* turn, the single turn that just left the window is folded into the running summary via one LLM call over `existing summary + that one turn`. Cost is small and ~constant regardless of conversation length. (Replaced Phase 5's bulk-at-threshold approach; `MEMORY_SUMMARIZE_AFTER` was removed.) Best-effort: on LLM error the fold is skipped and retried next turn.
- **Retrieval reuse (Phase 8):** the deterministic non-LLM cosine check described in §6.2 step 3. Threshold **0.72**, deliberately conservative because on this corpus cosine cannot cleanly separate "same fact" (≈0.63) from "adjacent topic" (≈0.67); a wrong reuse produces a wrong "I don't know" while a missed reuse only costs one retrieval. Chunk **text** (not embeddings) is stored and re-embedded on demand.

---

## 12. Configuration reference (env vars)

All read in `app/config/settings.py`. The annotated list is `.env.example`. Defaults in parentheses are the code defaults; production turns several features on by environment (see PRODUCT_STATUS.md).

| Group | Vars |
|---|---|
| **LLM** | `LLM_ADAPTER`, `LLM_MODEL`, `LLM_API_KEY`. `LLM_BASE_URL` only for `custom`. Boot check: `LLM_CONFIG_CHECK`. |
| **Model choice** | `CLAUDE_API_KEY` for the picker's Claude models; `LLM_AUX_*` for background work. A company's own model is stored per org, not an env model string. |
| **Embeddings / rerank** | `EMBEDDING_BACKEND` (local), `RERANKER_BACKEND`, `RETRIEVAL_CANDIDATE_POOL` (40), `RETRIEVAL_RRF_K` (60), `RAG_TOP_K` (5) |
| **Database** | `DATABASE_URL`, `EMBEDDING_DIM` (1024) |
| **Chunking** | `CHUNK_SIZE` (256), `CHUNK_OVERLAP` (40), `CHUNK_MAX_CHARS` (4000), `CHUNK_TOKEN_BACKEND` (heuristic) |
| **RAG** | `RAG_SIMILARITY_THRESHOLD` (0.35), `RETRIEVAL_REUSE_THRESHOLD` (0.72) |
| **Second Brain** | `GRAPH_RETRIEVAL_ENABLED` (false), `LIVE_TOOLS_ENABLED` (false), `LIVE_TOOLS_PROVIDERS` (linear), `PERSONAL_MEMORY_ENABLED` (false) |
| **Safety** | `GUARD_MODE` (off), `GUARD_API_KEY` + `GUARD_BASE_URL` (both or neither), `GUARD_SAFEGUARD_MODEL`, `RAG_AUDIT_ENABLED` (false), `MASK_IDENTIFIERS` (true) |
| **Limits** | `RATE_LIMIT_*`, `REDIS_URL`, `CHAT_MAX_CONCURRENT` (20), `CHAT_MAX_CONCURRENT_PER_PERSON` (2), `AI_TOKEN_LIMITS`, `AI_PRICES`, `AI_PERSON_DOLLARS_PER_DAY` (0.30), `AI_COMPANY_DOLLARS_PER_DAY` (3.00); 0 turns a limit off |
| **Tables** | `DOCTABLES_TEXT_ENABLED` (true), `DOCTABLES_ANSWERS_ENABLED` (false), `DOCTABLES_BACKFILL_BATCH` (20) |
| **Agent mode** | `ACTION_TTL_SECONDS` (900), `ACTION_RATE_PER_HOUR` (100), `ACTIONS_BLOCK_WIDER` (false), `MCP_*`, `HANDBOOK_MCP_URL` |
| **Email** | `EMAIL_SENDER` (`console` in code; `smtp`, `resend` or `sendgrid` for real mail) |
| **Install** | `HANDBOOK_DEPLOYMENT` (`cloud` or `selfhosted`), `HANDBOOK_VERSION`, `UPDATE_CHECK_ENABLED`, `UPDATER_URL` + `UPDATER_TOKEN`, `SIGNUP_ENABLED` (off in the self-hosted compose) |
| **Freshness** | `INTERNAL_TICK_SECRET`; webhook secrets for Slack, Linear, Notion; `DRIVE_PUSH_BASE_URL` |
| **Files** | Cloudinary cloud, key and secret — all set, or none (text then kept in Postgres) |

---

## 13. How to run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # LLM key and the rest; never commit .env
docker compose up -d          # Postgres + pgvector, and Valkey
python scripts/init_db.py
uvicorn app.api.main:app --reload
cd frontend && npm install && npm run dev
```

Organizations connect Notion, Drive, Slack, Linear, and GitHub from the Sources page. Syncing then runs on its own. `python scripts/cli.py` is the terminal client. Tests:

```bash
pytest -m "not network and not live_llm"
```

---

## 14. Extension points

- **Swap the LLM:** set `LLM_ADAPTER` to a name in `app/llm/adapters.py`. Do not invent a base URL that fights the adapter.
- **Add an indexed source:** a `SourceAdapter` plus a factory branch, and a decision about how that source's sharing is captured. GitHub is the pattern for a source that must not be embedded.
- **Add an agent:** `Agent.answer()` → `AgentResponse`, registered with routing. A misroute must cost a refusal, not an answer from the wrong corpus.
- **Swap embeddings, reranker, or web search:** a new impl behind the existing `base.py` and factory. An embedding-width change is a schema change and a re-ingest.

---

## 15. Phase history (what was built when)

| Phase | Delivered |
|---|---|
| 1 | LLM + embedding provider abstractions, config, core exceptions |
| 2 | Postgres/pgvector schema behind a pooled connection layer, preprocessing + chunking, `VectorStore`. Multi-tenant isolation test |
| 3 | RAG query path: embed → org-scoped retrieve → confidence gate → strict grounded prompt → answer. Two-layer anti-hallucination |
| 4 | First external source: Notion (`SourceAdapter` + `NotionAdapter`), `ingest_source` pipeline |
| 5 | (A) Conversation memory (query rewrite + running summary); (B) Web-search fallback (tool-calling, labelled, graceful degradation) |
| 6 | Better retrieval under the unchanged gate: contextual retrieval (ingest), hybrid vector+keyword RRF, cross-encoder reranking |
| 7 | (A) Formal `PolicyAgent` behind `Agent`; (B) Golden-set evaluation (path-firing tier + RAGAS tier) wired into CI |
| 8 | (A) Incremental summarization; (B) retrieval reuse (deterministic non-LLM cosine check) + `conversation_last_retrieval` table |
| 9 | (A) Single interactive `rich` CLI over `PolicyAgent` (retired `ask.py`/`chat.py`); (B) per-organization Notion credentials (`NOTION_TOKEN_<NAME>`, `resolve_token`, no fallback) + `list_organizations` |
| 10+ | HTTP API + Next.js portal, magic-link auth, OAuth per connector, signup approval, ingestion queue; Drive, Slack, Linear adapters; GitHub live reads; spaces; per-source routing; Ask in Slack; scheduled reports; multi-model + BYOM; automatic sync; charts in Ask; attachments in Cloudinary; feedback and gaps; needs-attention bell; prompt-injection defense; Second Brain (graph, live reads, personal memory); document-level access control; push sync (PR #44, 2026-10-01); inline citations; open-ended charts with document tables, paragraph figures and uploads (PR #45, #50–#52, 2026-10-13). See `docs/handbook/PRODUCT_STATUS.md` and `CLAUDE.md`. |
| Oct–Nov 2026 | Deep analysis Extra effort (#53, #55); Agent mode (#54), later through MCP only, with Handbook's own Slack and Google MCP servers; identifier masking (#57); `chunks` partitioned per organization with RLS; `halfvec` embeddings (#60); self-hosted images, installer and Updates (0.2.0–0.2.1); rate limits in Redis (#65); each company's own Google and Slack app (#64); sheet rows, table answers and two-sheet comparisons (#66); answer cache removed; company email domain; AI limits in dollars (#67) and tokens a minute per model (#68); injection guard on its own account; chat rename and pin; dismissable notifications; release notes and new-version pill (0.3.0–0.4.1). |

---

## 16. Known limitations, gotchas & open issues

**Gotchas (see CLAUDE.md §5 for the full list):**
- Migration must **not** use the connection pool (the pool's `configure` runs `register_vector`, which needs the `vector` extension to already exist).
- Contextual retrieval changes stored `content` (chunk = `"<context>\n\n<original>"`), so displayed chunks include the prefix, and stored size exceeds the raw source.
- The reranker downloads ~2.2 GB on first use, then caches.
- Notion tokens are per-org and must **not** fall back; a page must be **explicitly shared** with the integration or `list_documents()` returns zero pages.
- The Phase 3 grounding test fixture disables memory + web search for determinism.
- Reuse fires rarely on a small corpus by design (0.72 threshold).

**Resolved since first written:** retrieval recall on typo'd queries (query normalization,
`app/rag/query_normalize.py`, plus hybrid search) and over-inference on ambiguous text (the
three-mode grounded prompt, §6.2 step 6).

---

## 17. Not built yet

See `docs/handbook/PRODUCT_STATUS.md` for the current list. In short: Notion per-page access
(no provider API), Google Groups in production (needs a Workspace-admin connection), more
connectors (Confluence, Jira, Zendesk, Salesforce, SharePoint), NLI proof that a cited sentence is supported (inline citations are built),
and images in chat.
