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
  cli.py                 # interactive chat over the PolicyAgent (Phase 9)
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
python scripts/ingest_notion.py --org "Acme Corp"   # prints the new org_id
python scripts/cli.py <org_id>                       # chat with it (Phase 9 CLI)
```

A grounded answer that traces back to your real Notion page confirms the whole
pipeline (fetch → chunk → embed → store → retrieve → generate) is wired end to
end.

## Conversation memory & web-search fallback (Phase 5)

Two independent additions to the query path, both off by default in the Phase 3
`rag` fixture but on via `build_rag_pipeline()`:

- **Conversation memory** (`app/memory/`) — pass a `conversation_id` and a
  context-dependent follow-up ("what about part-timers?") is rewritten into a
  standalone question (a cheap LLM call) using recent turns + a running summary,
  *before* the unchanged retrieve→gate→generate path. History is Postgres-backed
  and org-scoped; the most recent `MEMORY_RECENT_TURNS` (=3) turns stay verbatim,
  and each turn that falls out of that window is folded into the running summary
  *incrementally* (Phase 8 — one turn per update, no bulk threshold).
- **Web-search fallback** (`app/websearch/`) — when internal retrieval fails the
  gate, the model is offered a `web_search` tool (real function-calling). For a
  real, named *external* entity it runs exactly one bounded search and composes an
  answer clearly labelled as web-sourced (`RagResult.source == "web"`); internal
  questions still get the fixed fallback; timeouts/failures degrade to it too.
  Default provider is keyless **DuckDuckGo** (`WEB_SEARCH_PROVIDER`); **Tavily** is
  the documented production swap behind the same interface.

```bash
# both are exercised interactively in the single CLI (Phase 9): ask a follow-up
# in the same session (resolved against prior turns), or ask about an external
# entity not in the docs to get a clearly-labelled web answer.
python scripts/cli.py <org_id>
```

`RagResult.source` is `"policy"` (internal docs), `"web"` (web search), or
`"none"` (the fixed "I don't have information" fallback).

## Better retrieval: contextual + hybrid + reranking (Phase 6)

Plain top-k vector search ranks each chunk independently and can leave a relevant
chunk just outside the cutoff. Three techniques address this, all sitting *under*
the unchanged Phase 3 confidence gate:

1. **Contextual retrieval** (`app/ingestion/contextualize.py`, ingest-time) —
   prepend a short LLM-generated context to each chunk before embedding/storing,
   so it carries its situating meaning into both the vector and keyword indexes.
   One LLM call per chunk at ingest; zero query-time cost.
2. **Hybrid search** (`app/rag/retrieval.py`) — run vector *and* Postgres
   full-text (BM25-style) search and fuse them with **Reciprocal Rank Fusion**
   (rank-based, so no score normalization between cosine and `ts_rank`). Catches
   an exact term ("part-time", a form code, a product name) even when semantic
   similarity under-weights it.
3. **Cross-encoder reranking** (`app/reranker/`) — over-retrieve a 30-candidate
   pool then rerank with `BAAI/bge-reranker-v2-m3` (same family as BGE-M3, local,
   no new dependency) and take the final `top_k`.

These only change *which chunks, in what order* reach the prompt — the gate still
uses the best cosine similarity, so its threshold logic is unchanged. (MMR was
considered and deliberately not implemented.) See a before/after directly:

```bash
python scripts/compare_retrieval.py <org_id> "which internal form is used for reimbursement?"
```

Config (all optional, sensible defaults): `INGEST_CONTEXTUAL_ENABLED`,
`RETRIEVAL_HYBRID_ENABLED`, `RETRIEVAL_RERANK_ENABLED`, `RETRIEVAL_CANDIDATE_POOL`,
`RERANKER_MODEL`. The reranker downloads ~2.2GB on first use, then is cached.

## Cheaper multi-turn: incremental summaries + retrieval reuse (Phase 8)

Two refinements to the conversation path — the confidence gate, grounded
generation, and web-search fallback are all untouched:

1. **Incremental summarization** (`app/rag/pipeline.py`) — instead of keeping
   turns verbatim until a threshold trips and then bulk-summarizing, the running
   summary is updated after *every* turn by folding in only the single turn that
   just fell out of the verbatim window (`MEMORY_RECENT_TURNS`=3). Each update is
   one small LLM call over `existing summary + one turn`, so its cost stays
   roughly constant however long the conversation gets, and the summary is always
   current. (`MEMORY_SUMMARIZE_AFTER` no longer exists.)
2. **Retrieval reuse** (`app/rag/pipeline.py`) — before retrieval runs on a
   follow-up, a cheap **non-LLM** cosine check compares the rewritten question
   against the *previous* turn's retrieved chunks. If they clearly still cover it
   (`RETRIEVAL_REUSE_THRESHOLD`=0.72), those chunks are reused and hybrid
   search + rerank are skipped; otherwise retrieval runs as normal. The reuse
   similarity becomes the gate score, so reused chunks still pass through the
   unchanged gate → strict-prompt → generate path — reuse never bypasses grounding.
   The threshold is set high on purpose (a wrong reuse gives a wrong "I don't
   know"; a missed reuse only costs one retrieval), so on a small corpus it fires
   only for near-verbatim repeats — a starting point to validate against logged
   behaviour, like the 0.35 gate.

See both directly on ingested data (run twice for before/after):

```bash
python scripts/demo_phase8.py <org_id>                          # reuse ON
RETRIEVAL_REUSE_ENABLED=false python scripts/demo_phase8.py <org_id>  # reuse OFF
```

Config (all optional): `MEMORY_RECENT_TURNS`, `RETRIEVAL_REUSE_ENABLED`,
`RETRIEVAL_REUSE_THRESHOLD`.

## Interactive CLI + per-organization Notion credentials (Phase 9)

The closing phase of this build stage — a clean interface over everything above,
plus credential plumbing for genuinely separate organizations. No new RAG
behaviour. (Frontend, HTTP API, OAuth, and user/role handling are the *next*
stage, deliberately not built here.)

**One interactive CLI** (`scripts/cli.py`) replaces the old `ask.py`/`chat.py`. It
is a thin shell over the Phase 7 `PolicyAgent`: pick an org (or pass its id), then
chat in a loop until `/exit`. Each turn shows the internals behind the answer —
whether the question was rewritten from context, whether retrieval was reused,
whether the answer came from policy docs / web / the fallback, and the source
chunks that grounded it — formatted with `rich` (a presentation-only dependency
confined to this script; nothing in `app/` imports it).

```bash
python scripts/cli.py                 # pick an org from a list
python scripts/cli.py <org_id>        # chat as a specific org
```

**Per-organization Notion credentials.** Each real organization gets its *own*
Notion internal integration + secret, set as a distinctly-named env var
`NOTION_TOKEN_<NAME>`. Because a Notion integration can only see pages explicitly
shared with it, this makes each org's boundary a real access boundary enforced by
Notion — not just something the app keeps straight (and a faithful stand-in for
the per-customer OAuth that replaces it later). Config discovers the tokens
generically, so adding an org is one new env var + one ingestion run — never a
code change:

```bash
# .env
NOTION_TOKEN_ACME=ntn_...
NOTION_TOKEN_GLOBEX=ntn_...

# ingest each org with its own token (case-insensitive <name>)
python scripts/ingest_notion.py --org "Acme Corp"  --token acme
python scripts/ingest_notion.py --org "Globex Inc" --token globex
```

A named token resolves to *only* that org's secret — it never falls back to
another org's token or the default `NOTION_TOKEN` (which is used only when a run
names no token). Real multi-org data entry + ingestion happens after this phase.
