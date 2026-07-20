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
- **Everything is a swappable interface + factory.** Each capability (llm,
  embeddings, vectorstore) has: `base.py` (abstract contract), one or more
  concrete impls, a `factory.py` (`build_*` reads config and returns the impl),
  and an `__init__.py` exporting the public API. The rest of the app depends on
  the interface, never a concrete class.

## 3. Folder / file structure convention

```
app/
  config/       # typed settings (dataclasses w/ .from_env()). ONLY place that reads env.
  core/         # cross-cutting basics — the ProviderError exception hierarchy.
  llm/          # base.py (LLMProvider) + openai_provider.py + factory.py
  embeddings/   # base.py (EmbeddingProvider) + local.py + remote.py + factory.py
  db/           # Postgres plumbing: schema.sql, connection.py, migrate.py. Infra only.
  ingestion/    # preprocessing.py + chunking.py (text -> clean text -> chunks)
  vectorstore/  # base.py (VectorStore) + pgvector_store.py + factory.py
scripts/        # runnable entrypoints: verify_providers.py, init_db.py
tests/          # pytest; test_isolation.py is the Phase 2 completion gate
```

**Conventions to follow (match, don't reinvent):**
- New capability = new package with `base.py` + impl(s) + `factory.py` + `__init__.py`.
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
- **First-run migration + pgvector registration.** `get_connection` registers the
  pgvector type adapters, which requires the `vector` extension to already exist.
  On a brand-new database it won't yet, so registration is skipped gracefully
  during the first `apply_schema` (migrations don't pass vector params); later
  store connections register fine. Don't "fix" this by forcing registration.
- **Isolation is enforced by the `WHERE org_id` clause, not the HNSW index.** Keep
  it that way — never expose a query path that omits `org_id`.
- **Preprocessing scope.** We assume text/Markdown input. Layout-aware extraction
  from PDF/DOCX/HTML (Unstructured/Docling) is deliberately deferred to a future
  ingestion-adapters phase.

## 5. Database tables (keep this in sync as the schema evolves)

Defined in `app/db/schema.sql`. Current tables:

| Table           | Responsibility                                                        |
| --------------- | -------------------------------------------------------------------- |
| `organizations` | Tenants. Everything else hangs off an org. Columns: `id`, `name`, `created_at`. |
| `documents`     | A source policy file/upload, scoped to one org. `id`, `org_id`, `title`, `source_uri`, `created_at`. |
| `chunks`        | Text chunks + their `vector(1024)` embedding, scoped to one org. `id`, `org_id`, `document_id`, `chunk_index`, `content`, `embedding`, `created_at`. |

Deletes cascade: removing an org removes its documents and chunks. Indexes:
`org_id` on documents and chunks (tenant filter) + an HNSW cosine index on
`chunks.embedding` (ranking speed).

**No `users`, `auth`, or OAuth tables yet** — deliberately deferred to a later phase.

## 6. Current state: built vs. pending

**Built**
- Phase 1 — LLM & embedding provider abstraction (llm, embeddings, config, core).
- Phase 2 — DB schema (pgvector), preprocessing + chunking, vector store
  abstraction, and a passing multi-tenant isolation test.

**Pending (not started)**
- Retrieval/RAG pipeline: query → embed → retrieve → assemble context → LLM answer
  (with citations).
- Ingestion adapters: layout-aware extraction from PDF/DOCX/HTML.
- Users / auth / tenancy management (OAuth, API keys, roles).
- API layer (HTTP endpoints) and an orchestrator.
- Packaging the self-hosted Docker image.

---

_When you finish a phase: update sections 4, 5, and 6 (and 2/3 if conventions
changed) before committing._
