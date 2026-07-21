# RAG Engine

A multi-tenant RAG policy Q&A platform, built in small, reviewable phases.

> **Phases built so far:** (1) LLM & embedding provider abstraction,
> (2) database schema + chunking + vector store layer with proven tenant
> isolation, (3) the RAG query path (retrieve → gate → grounded generate),
> (4) the first real external source — Notion — ingested end to end. Auth/API
> and more source adapters (Drive, GitHub, Slack) are future phases. See
> [CLAUDE.md](CLAUDE.md) for the full project rulebook and current state.

## Provider abstraction layer

This gives the rest of the application one small, stable interface for LLM and
embedding calls, so it never touches provider-specific details directly. The app
depends on **interfaces** (`LLMProvider`, `EmbeddingProvider`); concrete
implementations are chosen by **factories** from config. Swapping an
implementation later requires no changes downstream.

The LLM side is **provider-agnostic** via the official `openai` client + a
`base_url` swap: OpenAI, Google Gemini, Anthropic/Claude, a self-hosted
OpenAI-compatible endpoint (like FreeLLMAPI), or a local model are all reachable
through the same `generate()` call. **Switching provider is a config change
(`LLM_MODEL` + `LLM_API_KEY` + `LLM_BASE_URL`) — no code changes.**

### Layout

```
app/
  config/
    settings.py          # one place that reads env -> typed settings
  core/
    exceptions.py        # shared ProviderError hierarchy
  llm/
    base.py              # LLMProvider interface (contract)
    openai_provider.py   # OpenAICompatProvider (openai client)
    factory.py           # build_llm_provider() -> LLMProvider
  embeddings/
    base.py              # EmbeddingProvider interface
    local.py             # LocalEmbeddingProvider (sentence-transformers)
    remote.py            # RemoteEmbeddingProvider (HTTP OpenAI-compatible)
    factory.py           # build_embedding_provider() -> EmbeddingProvider
scripts/
  verify_providers.py    # manual smoke test for both providers
.env.example             # expected environment variables

# future phases get their own top-level packages: app/rag/, app/db/, app/api/, ...
```

### Public API

```python
from app.llm import build_llm_provider
from app.embeddings import build_embedding_provider

llm = build_llm_provider()               # from env
print(llm.generate("Hello"))

embedder = build_embedding_provider()    # from env (local or remote)
vectors = embedder.embed(["some text"])  # list[list[float]]
```

- **`LLMProvider`** (interface) — `generate(prompt: str) -> str`. Implemented by
  `OpenAICompatProvider`.
- **`EmbeddingProvider`** (interface) — `embed(texts: list[str]) -> list[list[float]]`.
  Two implementations, interchangeable:
  - **`LocalEmbeddingProvider`** — runs BGE-M3 **in-process** via
    `sentence-transformers`: $0, no key, text never leaves the machine (default).
  - **`RemoteEmbeddingProvider`** — a remote OpenAI-compatible embeddings
    endpoint (e.g. DeepInfra) over HTTP.

All implementations convert underlying errors (timeouts, connection failures,
model load failures, API errors) into a clear custom `ProviderError`, so callers
only need to catch one exception type.

### Why the `openai` client and not a multi-provider library?

The `openai` client speaks the OpenAI wire format, which OpenAI, Gemini, and
Anthropic all expose an endpoint for — so a `base_url` swap is enough for basic
chat, with the fewest dependencies. The Gemini and Anthropic OpenAI-compatible
endpoints are vendor-labelled **beta/testing** (no prompt caching, some params
dropped). If we later need those native features or built-in fallbacks, the
`LLMProvider` interface + factory let us drop in a LiteLLM-backed implementation
without touching the rest of the app.

### Configuration

Copy the example env file and fill in real values:

```bash
cp .env.example .env
```

| Variable            | Purpose                                        | Example                    |
| ------------------- | ---------------------------------------------- | -------------------------- |
| `LLM_MODEL`         | Model id                                        | `auto`, `gpt-5`, `claude-sonnet-5` |
| `LLM_API_KEY`       | API key for the chosen provider                | `freellmapi-…`             |
| `LLM_BASE_URL`      | Endpoint; empty = OpenAI default                | `http://localhost:3001/v1` |
| `EMBEDDING_BACKEND` | `local` (default) or `remote`                   | `local`                    |
| `EMBEDDING_MODEL`   | Embedding model                                 | `BAAI/bge-m3`              |
| `EMBEDDING_DEVICE`  | Local only; optional device override            | `cpu` / `cuda` / `mps`     |
| `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` | Remote backend only         | DeepInfra key / URL        |

**Switching LLM provider** — set these three vars (see `.env.example` for
copy-paste blocks):

| Goal                     | `LLM_MODEL`         | `LLM_BASE_URL`                                            |
| ------------------------ | ------------------- | -------------------------------------------------------- |
| FreeLLMAPI / self-hosted | `auto`              | `http://localhost:3001/v1`                               |
| Native OpenAI            | `gpt-5`             | *(empty — default)*                                      |
| Google Gemini            | `gemini-3.5-flash`  | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| Anthropic / Claude       | `claude-sonnet-5`   | `https://api.anthropic.com/v1/`                          |

`.env` is git-ignored and must never be committed.

### Install & run the verify script

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/verify_providers.py
```

The script builds both providers from your `.env`, sends one test prompt and one
test embedding call, and prints the results. The first run of the local
embedding backend downloads the BGE-M3 weights (~2.2 GB), so it may take a
minute. Exit code `0` means both checks passed.

## Vector store layer (Phase 2)

Durable, **tenant-isolated** storage and retrieval of document chunks and their
embeddings, backed by Postgres + [pgvector](https://github.com/pgvector/pgvector).
Same "swappable interface + factory" pattern as the provider layer: the app talks
to `VectorStore`, never to Postgres directly.

```
app/
  db/
    schema.sql           # organizations, documents, chunks (+ pgvector)
    connection.py        # get_connection() — opens psycopg conn, registers pgvector
    migrate.py           # apply_schema() — idempotent
  ingestion/
    preprocessing.py     # preprocess(text) — normalize before chunking
    chunking.py          # chunk_text(text) — structure-aware, size+overlap configurable
  vectorstore/
    base.py              # VectorStore interface + RetrievedChunk
    pgvector_store.py    # PgVectorStore — every read/write requires org_id
    factory.py           # build_vector_store() -> VectorStore
scripts/
  init_db.py             # create extension + apply schema
tests/
  test_isolation.py      # PROOF that one org never sees another's data
```

### Public API

```python
from app.ingestion import preprocess, chunk_text
from app.embeddings import build_embedding_provider
from app.vectorstore import build_vector_store

store = build_vector_store()
embedder = build_embedding_provider()

org_id = store.create_organization("Acme Corp")
chunks = chunk_text(preprocess(raw_policy_text))
store.add_document(org_id, "Leave Policy", chunks, embedder.embed(chunks))

hits = store.query(org_id, embedder.embed(["how many leave days?"])[0], top_k=3)
# every hit is guaranteed to belong to org_id — no cross-tenant leakage
```

### Multi-tenant isolation

Every organization-scoped table has an `org_id`, and **every** vector-store
read/write requires one. Retrieval filters `WHERE org_id = ...` before ranking,
so isolation is enforced by the query itself — not by the vector index. This is
verified by `tests/test_isolation.py`, which creates multiple orgs with
*semantically near-identical* content and asserts that querying one org never
returns another's rows.

### Database setup & running the tests

You need a Postgres with the `pgvector` extension. A `docker-compose.yml` is
included for this:

```bash
# 1) start Postgres + pgvector (uses the DATABASE_URL from .env.example)
#    on first run give it a few seconds to initialize before the next step
docker compose up -d

# 2) create the extension + tables (idempotent)
python scripts/init_db.py

# 3) run the isolation test (needs DATABASE_URL; skipped if unset)
pytest tests/ -v

# stop it later (add -v to also wipe the data volume)
docker compose down
```

Any Postgres with pgvector works — the code only reads `DATABASE_URL`, so a
managed instance or a local Homebrew `postgresql@17` + `pgvector` are equally
fine. The embedding column is `vector(1024)` to match BGE-M3 — see
[CLAUDE.md](CLAUDE.md) if you change models.

## RAG query path (Phase 3)

`app/rag/` composes the pieces above into grounded, tenant-scoped Q&A: a question
+ `org_id` → embed → org-scoped retrieve → **confidence gate** → **strict grounded
prompt** → LLM answer, returned as a `RagResult` (`answer`, `answered`, `sources`,
`top_score`). Two independent layers keep answers grounded: the gate refuses
cheaply when nothing retrieved is even on-topic; the prompt refuses when on-topic
context doesn't actually answer. `tests/test_grounding.py` is the completion gate.

```python
from app.rag import build_rag_pipeline

rag = build_rag_pipeline()                       # from env
result = rag.answer("How many leave days do we get?", org_id=org_id)
print(result.answer, result.answered, result.top_score)
```

## External sources — Notion (Phase 4)

`app/sources/` fetches real content from external systems behind one interface,
`SourceAdapter` (`list_documents` / `fetch_document` / `get_last_modified`), so
Google Drive, GitHub, and Slack can later implement the *same* contract. The
first implementation is `NotionAdapter`, built on the official
[`notion-client`](https://github.com/ramnes/notion-sdk-py) SDK (only dep:
`httpx`) — chosen over `llama-index-readers-notion` to stay dependency-light and
keep full control of Notion block→text conversion (that conversion lives inside
the adapter). `app/ingestion/pipeline.py::ingest_source` wires an adapter into the
existing chunk → embed → store path, scoped to one org.

```
app/
  sources/
    base.py              # SourceAdapter interface + SourceRef / SourceDocument
    notion.py            # NotionAdapter (notion-client; block -> text)
    factory.py           # build_source_adapter("notion") -> SourceAdapter
  ingestion/
    pipeline.py          # ingest_source(adapter, org_id) -> IngestResult
scripts/
  ingest_notion.py       # pull every shared Notion page into a new org
  ask.py                 # run one grounded question against an org
```

### Auth (this phase: internal integration token)

Full multi-tenant OAuth needs an app to host the consent redirect (a later
phase), so this phase uses a Notion **internal integration secret** — a single
static token. A public integration's client id/secret are OAuth-only and cannot
be used as an API token; they're read into config but unused for now.

### Setup & run

1. Create a Notion **internal** integration at
   <https://www.notion.so/my-integrations>, copy its **Internal Integration
   Secret** into `.env` as `NOTION_TOKEN`.
2. In Notion, open your page → `•••` → **Connections** → add the integration
   (without this, the integration sees no pages).
3. Ingest, then ask:

```bash
python scripts/ingest_notion.py "Acme Corp"     # prints the new org_id
python scripts/ask.py <org_id> "How many days of paid annual leave do we get?"
```

A grounded answer that traces back to your real Notion page confirms the whole
pipeline (fetch → chunk → embed → store → retrieve → generate) is wired end to
end.
