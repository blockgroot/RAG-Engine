# Handbook (RAG Engine)

A multi-tenant AI assistant that answers employees' questions from their company's own
tools — **Notion, Google Drive, Slack, Linear and GitHub** — grounded strictly in that
company's content, never another tenant's and never the model's outside knowledge.

**Current status, every feature, blockers and testing: [PRODUCT_STATUS.md](docs/handbook/PRODUCT_STATUS.md).**
A plain walk through each feature: [current-features.md](docs/handbook/current-features.md).

## Features

- **Grounded Ask** — hybrid vector + keyword retrieval, re-ranking, a confidence gate and a
  strict prompt; honest "I don't know" and "not shared with you" refusals; follow-ups,
  whole-space summaries, Deep analysis (with an Extra effort setting) for questions that span
  documents, and web search for external topics.
- **Citations** — one document is named once under the answer, and that line opens it.
  Two or more documents keep a small number on each sentence, matched to the same list.
  The link is the address saved when the page was indexed, never one the model wrote.
  Slack shows the text without the numbers.
- **Connectors** — Notion, Google Drive (Docs, PDF, Word, Sheets, Excel, CSV), Slack, Linear
  (indexed) and GitHub (read live, never stored). Google and Slack can use the company's own app,
  set up under Sources.
- **Access control** — company and space isolation, plus each tool's own sharing: Drive per
  file, Slack per private channel, Linear per private team, GitHub per private repository;
  charts, reports, live reads and the knowledge graph follow the same rules. Only addresses
  on the company's email domain can be invited.
- **Instant updates** — webhooks from Slack, Linear and Notion and Drive push channels, with an
  hourly re-check as the floor.
- **Second Brain** — a knowledge graph across tools, live re-reads of current state, and
  personal memory.
- **Charts** (Chart mode) — from tool activity, tables inside documents and spreadsheets,
  figures written in paragraphs, and uploaded files. The model fills in a request; code
  checks it and SQL adds up stored rows, so no number comes from the model. Drawn with ECharts
  on the web; in Slack, a PNG with a link to the interactive chart.
- **Agent mode** — drafts writes into connected tools (a Linear issue, a Slack post, a Google
  Doc) and runs each only after the person approves its card. Every write goes through an MCP
  server an admin added, with the person's own sign-in.
- **File uploads in chat** — used together with company documents; stored privately in
  Cloudinary, or in Postgres when no Cloudinary keys are set.
- **Ask in Slack**, **scheduled reports** by email, **spaces**, **feedback and
  documentation-gap tracking**, a **notification bell**, and **chat history** (rename, pin).
- **Usage limits** — request rate limits, a cap on answers written at once, and AI limits in
  tokens a minute per model and dollars a day, for each person and each company.
- **Security** — magic-link sign-in, encrypted tokens, signed webhooks, layered
  prompt-injection defense (policy file, scrubbing, link provenance, canary, safety model), and
  personal identifiers masked before any text reaches an outside AI service.
- **Cloud or self-hosted** — a self-hosted install runs from Docker images; admins see new
  releases and update from Admin › Updates.

## Architecture

Every capability (LLM, embeddings, vector store, reranker, sources, auth, web search) is a
small interface with concrete implementations selected by a `build_*()` factory from config,
so swapping a provider is a config change, not a code change.

```
app/
  config/       typed settings — the only place env is read
  core/         shared exception types, streaming helpers
  llm/          OpenAI-compatible client, adapters, model catalog, per-request routing, pacing
  privacy/      personal identifiers masked before text leaves
  embeddings/   local (sentence-transformers) or remote embedding backend
  reranker/     local or remote cross-encoder
  db/           Postgres schema + pooled connection
  ingestion/    preprocessing, chunking, contextualization
  vectorstore/  pgvector storage, hybrid search, the Viewer (access) model
  rag/          query path: rewrite -> retrieve -> gate -> generate -> cite -> audit
  memory/       conversation history, summaries, personal memory
  websearch/    external web search fallback (DuckDuckGo)
  sources/      Notion / Drive / Slack / Linear adapters, Google Groups, Drive push channels
  githublive/   GitHub live reads + per-asker repository access
  agent/        per-source agents, routing, LangGraph orchestration
  graph/        Second Brain knowledge graph (identities, builder, walk, plan)
  livetools/    Second Brain live connector reads
  insights/     charts: metric registry, query grammar, resolver, SQL store, table answers
  doctables/    tables and paragraph figures inside documents and uploads, saved as rows
  chartrender/  PNG charts for Slack
  attachments/  chat uploads: extraction, limits, blob store
  actions/      Agent mode: drafting loop, approval cards, MCP client and presets
  mcphost/      Handbook's own MCP servers for Slack and Google
  feedback/     answer ratings + documentation gaps, grouped into topics
  schedulers/   scheduled reports
  guard/        prompt-injection scoring and answer moderation
  security/     untrusted-text policy, link provenance, visibility predicate, rate and AI limits
  ratelimit/    token buckets, in Redis or in process
  auth/         magic links, OAuth per connector, company apps, sessions, email
  jobs/         ingestion queue, worker, automatic sync
  workspaces/   spaces and membership
  updates/      release check and release emails (self-hosted)
  api/          FastAPI routes, incl. Slack events, webhooks and the MCP host
scripts/        CLI, ingestion, worker and verification entrypoints
frontend/       Next.js portal
deploy/         self-hosted compose package and updater
release-notes/  one file per release
tests/          pytest suite
evaluation/     golden-set regression evaluation (+ RAGAS) and benchmarks
```

See [CLAUDE.md](CLAUDE.md) for the constraints behind each decision.

## Tech stack

- **Backend:** Python, FastAPI
- **Database:** PostgreSQL + [pgvector](https://github.com/pgvector/pgvector) (embeddings as
  `halfvec(1024)`; `chunks` partitioned per organization, with row-level security)
- **Rate limits:** Redis-compatible store (Valkey) when `REDIS_URL` is set, else per process
- **Embeddings:** BGE-M3 locally, or a remote OpenAI-compatible embedding API
- **LLM:** any provider on the fixed `LLM_ADAPTER` list (Claude, OpenAI, Gemini, …). The picker
  offers Claude Sonnet 5.5 and Claude Haiku 5.5; background work runs on a separate aux model
  (`LLM_AUX_*`). A company can also add its own model.
- **Storage & email:** Cloudinary for uploads (optional); email through SMTP, Resend or SendGrid
- **Frontend:** Next.js (App Router), plain CSS

## Getting started

### Prerequisites

- Python 3.10+ (the images use 3.12)
- PostgreSQL with the `pgvector` extension 0.8+ (a `docker-compose.yml` with Postgres and
  Valkey is included)
- Node.js 18+ (only needed for the frontend)

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # fill in your LLM key and other settings

docker compose up -d      # start Postgres + pgvector, and Valkey
python scripts/init_db.py # create the schema
```

### Running

```bash
# Interactive CLI chat
python scripts/cli.py

# HTTP API
# With EMBEDDING_BACKEND=local or RERANKER_BACKEND=local, drop --reload for
# anything perf-sensitive: every file-save respawn reloads the multi-GB
# BGE-M3/reranker weights.
uvicorn app.api.main:app --reload

# Separate ingestion worker (optional: the API drains the queue unless INGEST_WORKER_IN_API=false)
python scripts/run_worker.py

# Frontend (proxies /api/* to uvicorn via next.config.js)
cd frontend && npm install && npm run dev
```

### Ingesting content

```bash
# One-off manual ingest from Notion (per-org token)
python scripts/ingest_notion.py --org "Acme Corp" --token acme
```

Organizations connect Notion, Google Drive, Slack, Linear and GitHub from the Sources page
once the API and frontend are running; syncing then runs on its own.

A self-hosted install (one company per server) uses the compose package in `deploy/selfhost/`;
its guides are in `deploy/selfhost/docs/` (install, connectors, backup, upgrade).

## Configuration

All configuration is read from environment variables — see `.env.example`
for the full list with inline documentation. The essentials:

| Variable            | Purpose                                    |
| ------------------- | ------------------------------------------- |
| `LLM_ADAPTER` / `LLM_MODEL` / `LLM_API_KEY` | LLM provider (fixed list, supplies the URL) + model; `LLM_BASE_URL` only for `custom`; checked at boot (`LLM_CONFIG_CHECK`) |
| `LLM_AUX_ADAPTER` / `LLM_AUX_MODEL` / `LLM_AUX_API_KEY` | Background model (ingest, figures, grouping); key and endpoint both or neither |
| `CLAUDE_API_KEY` | The picker's Claude models (falls back to `LLM_API_KEY` when `LLM_ADAPTER` is Claude) |
| `EMBEDDING_BACKEND` | `local` (default) or `remote`               |
| `DATABASE_URL`      | Postgres connection string                  |
| `REDIS_URL`         | Rate limits shared across servers; empty keeps them per process |
| `AI_TOKEN_LIMITS` / `AI_PRICES` / `AI_*_DOLLARS_PER_DAY` | AI limits: tokens a minute per model, dollars a day per person and company |
| `AUTH_JWT_SECRET`   | Session signing key                         |
| `AUTH_ENCRYPTION_KEYS` | OAuth token encryption key(s)             |
| `FRONTEND_URL` / `API_CORS_ORIGINS` | Frontend origin (magic links, CORS) |
| `NEXT_PUBLIC_API_BASE_URL` / `API_PROXY_TARGET` | Frontend `/api` rewrite → FastAPI (see `frontend/.env.example`) |
| `EMAIL_SENDER` | `smtp`, `resend` or `sendgrid` (`console` prints links locally, for development only) |
| `INTERNAL_TICK_SECRET` | Authenticates the external tick that drives automatic sync |
| `SLACK_SIGNING_SECRET`, `LINEAR_WEBHOOK_SECRET`, `NOTION_WEBHOOK_VERIFICATION_TOKEN`, `DRIVE_PUSH_BASE_URL` | Instant updates; each receiver is closed until set |
| `GUARD_MODE` / `GUARD_API_KEY` / `GUARD_BASE_URL` | Prompt-injection guard, on its own account if set |
| `MASK_IDENTIFIERS` | On by default; `false` is the kill switch for identifier masking |
| `HANDBOOK_DEPLOYMENT` / `HANDBOOK_VERSION` | `cloud` or `selfhosted`, and the version running (Admin › Updates) |

`.env` is git-ignored and must never be committed.

## Testing

```bash
pytest -m "not network and not live_llm"   # what CI runs
```

Golden-set regression evaluation (path-firing checks + optional RAGAS
scoring) lives under `evaluation/` — see `evaluation/run_eval.py`.
