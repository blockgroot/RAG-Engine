# Handbook (RAG Engine)

A multi-tenant AI assistant that answers employees' questions from their company's own
tools — **Notion, Google Drive, Slack, Linear and GitHub** — grounded strictly in that
company's content, never another tenant's and never the model's outside knowledge.

**Current status, every feature, blockers and testing: [PRODUCT_STATUS.md](docs/handbook/PRODUCT_STATUS.md).**
A plain walk through each feature: [current-features.md](docs/handbook/current-features.md).

## Features

- **Grounded Ask** — hybrid vector + keyword retrieval, re-ranking, a confidence gate and a
  strict prompt; honest "I don't know" and "not shared with you" refusals; follow-ups,
  whole-space summaries, web search for external topics, per-question model choice and
  bring-your-own-model.
- **Connectors** — Notion, Google Drive (Docs, PDF, Word), Slack, Linear (indexed) and GitHub
  (read live, never stored); Google Forms for sentiment charts only.
- **Access control** — tenant and space isolation, plus each tool's own sharing: Drive per
  file, Slack per private channel, Linear per private team, GitHub per private repository;
  charts, reports, live reads and the knowledge graph follow the same rules.
- **Instant updates** — webhooks from Slack, Linear and Notion and Drive push channels, with an
  hourly re-check as the floor.
- **Second Brain** — a knowledge graph across tools, live re-reads of current state, and
  personal memory.
- **Charts in Ask** — numbers from SQL over recorded activity, never from the model.
- **File uploads in chat** — stored privately in Cloudinary, used together with company documents.
- **Ask in Slack**, **scheduled reports** by email (SendGrid), **spaces**, **feedback and
  documentation-gap tracking**, a **needs-attention bell**, and **chat history**.
- **Security** — magic-link sign-in, encrypted tokens, signed webhooks, and layered
  prompt-injection defense (policy file, scrubbing, link provenance, canary, safety model).

## Architecture

Every capability (LLM, embeddings, vector store, reranker, sources, auth, web search) is a
small interface with concrete implementations selected by a `build_*()` factory from config,
so swapping a provider is a config change, not a code change.

```
app/
  config/       typed settings — the only place env is read
  core/         shared exception types
  llm/          OpenAI-compatible client, adapters, per-request model routing, pacing
  embeddings/   local (sentence-transformers) or remote embedding backend
  reranker/     local or remote cross-encoder
  db/           Postgres schema + pooled connection
  ingestion/    preprocessing, chunking, contextualization
  vectorstore/  pgvector storage, hybrid search, the Viewer (access) model
  rag/          query pipeline: rewrite -> retrieve -> gate -> generate -> audit
  memory/       conversation history, summaries, personal memory
  websearch/    external web search fallback (DuckDuckGo)
  sources/      Notion / Drive / Slack / Linear adapters, Google Groups, Drive push channels
  githublive/   GitHub live reads + per-asker repository access
  agent/        per-source agents, routing, LangGraph orchestration
  graph/        Second Brain knowledge graph (identities, builder, walk, plan)
  livetools/    Second Brain live connector reads
  insights/     charts: metric registry, facts, SQL store, resolver
  attachments/  chat uploads: extraction, limits, Cloudinary blob store
  feedback/     answer ratings + documentation gaps
  schedulers/   scheduled reports
  guard/        prompt-injection scoring and answer moderation
  security/     untrusted-text policy, scrubbing, link provenance, visibility predicate
  auth/         magic links, OAuth per connector, sessions, email, email change
  jobs/         ingestion queue, worker, automatic sync
  workspaces/   spaces (sub-workspaces) and membership
  api/          FastAPI routes, incl. Slack events and webhooks
scripts/        CLI, ingestion, worker and verification entrypoints
frontend/       Next.js portal
tests/          pytest suite
evaluation/     golden-set regression evaluation (+ RAGAS)
```

See [CLAUDE.md](CLAUDE.md) for the design rationale behind each decision.

## Tech stack

- **Backend:** Python, FastAPI
- **Database:** PostgreSQL + [pgvector](https://github.com/pgvector/pgvector)
- **Embeddings:** BGE-M3 locally, or a remote OpenAI-compatible embedding API (e.g. Jina)
- **LLM:** any provider on the fixed `LLM_ADAPTER` list (Gemini, Groq, OpenAI, Anthropic, …);
  OpenRouter and Groq for per-question model choice
- **Storage & services:** Cloudinary (uploads), SendGrid (email), Render (API), Vercel (frontend)
- **Frontend:** Next.js (App Router), plain CSS

## Getting started

### Prerequisites

- Python 3.11+
- PostgreSQL with the `pgvector` extension (a `docker-compose.yml` is
  included)
- Node.js 18+ (only needed for the frontend)

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # fill in your LLM key and other settings

docker compose up -d      # start Postgres + pgvector
python scripts/init_db.py # create the schema
```

### Running

```bash
# Interactive CLI chat
python scripts/cli.py

# HTTP API
# --reload is fine with EMBEDDING_BACKEND=remote / RERANKER_BACKEND=remote
# (the default in .env.example). If you switch either to `local`, drop
# --reload for anything perf-sensitive: every file-save respawn re-triggers
# startup model warmup and reloads the multi-GB BGE-M3/reranker weights,
# which can push a 16GB machine into swap (see CLAUDE.md §4).
uvicorn app.api.main:app --reload

# Ingestion worker (for admin-triggered syncs)
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

## Configuration

All configuration is read from environment variables — see `.env.example`
for the full list with inline documentation. The essentials:

| Variable            | Purpose                                    |
| ------------------- | ------------------------------------------- |
| `LLM_ADAPTER` / `LLM_MODEL` / `LLM_API_KEY` | LLM provider (fixed list, supplies the URL) + model; `LLM_BASE_URL` only for `custom`; checked at boot (`LLM_CONFIG_CHECK`) |
| `EMBEDDING_BACKEND` | `local` (default) or `remote`               |
| `DATABASE_URL`      | Postgres connection string                  |
| `AUTH_JWT_SECRET`   | Session signing key                         |
| `AUTH_ENCRYPTION_KEYS` | OAuth token encryption key(s)             |
| `FRONTEND_URL` / `API_CORS_ORIGINS` | Frontend origin (magic links, CORS) |
| `NEXT_PUBLIC_API_BASE_URL` / `API_PROXY_TARGET` | Frontend `/api` rewrite → FastAPI (see `frontend/.env.example`) |
| `EMAIL_SENDER` | `sendgrid` in production (`console` prints links locally, for development only) |
| `INTERNAL_TICK_SECRET` | Authenticates the external tick that drives automatic sync |
| `SLACK_SIGNING_SECRET`, `LINEAR_WEBHOOK_SECRET`, `NOTION_WEBHOOK_VERIFICATION_TOKEN`, `DRIVE_PUSH_BASE_URL` | Instant updates; each receiver is closed until set |

`.env` is git-ignored and must never be committed.

## Testing

```bash
pytest -m "not network and not live_llm"   # what CI runs
```

Golden-set regression evaluation (path-firing checks + optional RAGAS
scoring) lives under `evaluation/` — see `evaluation/run_eval.py`.
