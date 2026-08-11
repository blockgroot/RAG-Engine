# RAG Engine

A multi-tenant Retrieval-Augmented Generation platform for company policy Q&A.
Each organization uploads its own documents; employees ask questions and get
answers grounded strictly in their own company's content — never another
tenant's, and never the model's outside knowledge.

## Features

- **Multi-tenant isolation** — every query is scoped by `org_id` (and
  optionally a nested `workspace_id`), enforced at the query level.
- **Grounded answers** — a confidence gate plus a strict prompt stop the
  model from answering off-topic or hallucinating; unanswerable questions get
  a fixed fallback instead of a guess.
- **Better retrieval** — contextual chunking, hybrid vector + keyword search
  (Reciprocal Rank Fusion), and cross-encoder reranking.
- **Conversation memory** — multi-turn chat with follow-up question
  rewriting and incrementally maintained summaries.
- **Web search fallback** — real, named external entities can be answered via
  a bounded web search, clearly labelled as non-internal.
- **External sources** — Notion and Google Drive out of the box, behind a
  common adapter interface for adding more.
- **Auth & admin** — magic-link login, per-org OAuth connections, an admin
  panel, and a durable ingestion job queue.
- **HTTP API + web frontend** — a FastAPI backend with streaming chat, and a
  Next.js portal for login, chat, and admin.

## Architecture

Every capability (LLM, embeddings, vector store, reranker, sources, auth,
web search) is a small interface with one or more concrete implementations,
selected by a `build_*()` factory from config — so swapping a provider is a
config change, not a code change.

```
app/
  config/       # typed settings, read from env
  core/         # shared exception types
  llm/          # LLM provider interface + OpenAI-compatible client
  embeddings/   # local (sentence-transformers) or remote embedding backend
  db/           # Postgres schema + pooled connection + migrations
  ingestion/    # preprocessing, chunking, contextualization
  vectorstore/  # pgvector-backed storage, hybrid search
  reranker/     # cross-encoder reranking
  rag/          # the query pipeline: retrieve -> gate -> generate
  memory/       # conversation history + summarization
  websearch/    # external-entity web search fallback
  sources/      # Notion / Google Drive adapters
  agent/        # PolicyAgent — the RAG pipeline behind a generic agent contract
  auth/         # magic-link login, OAuth, sessions
  jobs/         # ingestion job queue + worker
  workspaces/   # sub-workspace membership and scoping
  api/          # FastAPI app (auth, chat, admin, workspaces)
scripts/        # CLI, ingestion, worker, and demo entrypoints
frontend/       # Next.js portal
tests/          # pytest suite
evaluation/     # golden-set regression evaluation
```

See [CLAUDE.md](CLAUDE.md) for the detailed design rationale and full
project history.

## Tech stack

- **Backend:** Python, FastAPI
- **Database:** PostgreSQL + [pgvector](https://github.com/pgvector/pgvector)
- **Embeddings:** BGE-M3 (local via `sentence-transformers`, or remote)
- **LLM:** any OpenAI-compatible endpoint (OpenAI, Gemini, Claude, or
  self-hosted) via a `base_url` swap — no code changes
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

Organizations can also connect Notion or Google Drive from the admin panel
once the API and frontend are running.

## Configuration

All configuration is read from environment variables — see `.env.example`
for the full list with inline documentation. The essentials:

| Variable            | Purpose                                    |
| ------------------- | ------------------------------------------- |
| `LLM_MODEL` / `LLM_API_KEY` / `LLM_BASE_URL` | LLM provider selection |
| `EMBEDDING_BACKEND` | `local` (default) or `remote`               |
| `DATABASE_URL`      | Postgres connection string                  |
| `AUTH_JWT_SECRET`   | Session signing key                         |
| `AUTH_ENCRYPTION_KEYS` | OAuth token encryption key(s)             |
| `FRONTEND_URL` / `API_CORS_ORIGINS` | Frontend origin (magic links, CORS) |
| `NEXT_PUBLIC_API_BASE_URL` / `API_PROXY_TARGET` | Frontend `/api` rewrite → FastAPI (see `frontend/.env.example`) |

`.env` is git-ignored and must never be committed.

## Testing

```bash
pytest tests/ -v
```

Golden-set regression evaluation (path-firing checks + optional RAGAS
scoring) lives under `evaluation/` — see `evaluation/run_eval.py`.
