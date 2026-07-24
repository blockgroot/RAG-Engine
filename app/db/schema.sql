-- RAG Engine — database schema (Postgres + pgvector)
--
-- Multi-tenant policy Q&A store. Three tables, all organization-scoped so we can
-- prove strict isolation between tenants (see tests/test_isolation.py).
--
-- IMPORTANT: the embedding column dimension below (1024) MUST match the output
-- dimension of the configured embedding model (BGE-M3 = 1024). If the model
-- changes, this schema and DatabaseSettings.embedding_dim must change together.
-- See CLAUDE.md.

CREATE EXTENSION IF NOT EXISTS vector;

-- Tenants. Everything else hangs off an organization.
CREATE TABLE IF NOT EXISTS organizations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Source documents (one policy file / page / upload). Org-scoped.
CREATE TABLE IF NOT EXISTS documents (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    source_uri TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_documents_org ON documents (org_id);

-- Chunks of a document + their embedding vector. Org-scoped (denormalized org_id
-- so every retrieval query can filter by tenant without a join).
CREATE TABLE IF NOT EXISTS chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index INT  NOT NULL,
    content     TEXT NOT NULL,
    embedding   vector(1024) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tenant filter index: every read filters WHERE org_id = ... first.
CREATE INDEX IF NOT EXISTS idx_chunks_org ON chunks (org_id);

-- Approximate-nearest-neighbour index for cosine similarity search.
-- Correctness of isolation does NOT depend on this index — the org_id WHERE
-- clause guarantees it; the index only speeds up ranking within a tenant.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- Full-text search column for hybrid (keyword/BM25-style) retrieval (Phase 6).
-- GENERATED from content so it stays in sync automatically (existing rows get it
-- backfilled when this runs); the GIN index makes keyword lookups fast. Used
-- alongside vector search and fused via Reciprocal Rank Fusion — see
-- app/rag/retrieval.py.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX IF NOT EXISTS idx_chunks_content_tsv ON chunks USING gin (content_tsv);

-- Conversations (Phase 5): group a sequence of question/answer turns so a
-- follow-up can be resolved against prior context. Org-scoped like everything
-- else, so one tenant's conversation history is isolated from another's.
-- `summary` holds the running compression of older turns (see app/rag/pipeline).
CREATE TABLE IF NOT EXISTS conversations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    summary    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conversations_org ON conversations (org_id);

-- One question + its answer within a conversation. `org_id` is denormalized for
-- the same tenant-filter reason as chunks. Older turns may be pruned once
-- compressed into conversations.summary.
CREATE TABLE IF NOT EXISTS conversation_turns (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    org_id          UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    turn_index      INT  NOT NULL,
    question        TEXT NOT NULL,
    answer          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_turns_conversation ON conversation_turns (conversation_id);

-- Last-turn retrieved chunks (Phase 8): one row per conversation (upserted each
-- turn) holding the chunks retrieved on the most recent turn, as a JSON array of
-- {content, document_id, chunk_index, org_id}. The pipeline reads this to decide,
-- via a cheap non-LLM embedding-similarity check, whether the previous chunks
-- still cover the next question — if so it reuses them and skips retrieval.
-- Embeddings are NOT stored here (no vector column): they are recomputed from
-- `content` on demand, keeping the schema simple. Cascades with its conversation.
CREATE TABLE IF NOT EXISTS conversation_last_retrieval (
    conversation_id UUID PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    org_id          UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    chunks          TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Verified company email domains for an org (Phase 10). A domain only grants
-- automatic employee access once `verified_at` is set (DNS TXT check passed)
-- AND an admin has explicitly flipped `auto_join_enabled` — verification alone
-- never grants access, and there is no way to infer org membership from an
-- unverified domain claim. UNIQUE on domain: one org owns a given domain.
CREATE TABLE IF NOT EXISTS org_domains (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id            UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    domain            TEXT NOT NULL UNIQUE,
    verified_at       TIMESTAMPTZ,
    auto_join_enabled BOOLEAN NOT NULL DEFAULT false,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_org_domains_org ON org_domains (org_id);

-- Application users (Phase 10). `org_id` is nullable only for the brief window
-- before an org is resolved (domain match at signup); a user in that state is
-- NEVER issued a session (see app/api/auth.py) so a null-org row can't act on
-- any tenant's data. `role` gates admin-only endpoints.
CREATE TABLE IF NOT EXISTS users (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email      TEXT NOT NULL UNIQUE,
    org_id     UUID REFERENCES organizations (id) ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_org ON users (org_id);

-- Per-org, per-provider OAuth credentials (Phase 10) — replaces hand-set
-- NOTION_TOKEN_<NAME> env vars with an admin-driven OAuth connect flow.
-- Tokens are encrypted at rest (see app/security/crypto.py); this table never
-- stores plaintext. UNIQUE (org_id, provider) makes cross-tenant ambiguity
-- structurally impossible: every lookup MUST filter by org_id, and there is
-- at most one row per org per provider, so a query can never accidentally
-- return another org's connection by provider or workspace id alone.
CREATE TABLE IF NOT EXISTS oauth_connections (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    provider                TEXT NOT NULL,
    external_workspace_id   TEXT NOT NULL,
    access_token_encrypted  TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    expires_at              TIMESTAMPTZ,
    connected_by_user_id    UUID REFERENCES users (id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_oauth_connections_org ON oauth_connections (org_id);

-- Admin-triggered ingestion jobs (Phase 10/12). A durable, pollable record of a
-- background fetch->chunk->embed->store run so an admin sees progress instead
-- of a blocking script. Consumed by a Postgres-backed worker (app/jobs/), not
-- an in-process background task, so a crashed worker never loses the job.
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES oauth_connections (id) ON DELETE CASCADE,
    status        TEXT NOT NULL DEFAULT 'queued',
    doc_count     INT,
    error         TEXT,
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_org ON ingestion_jobs (org_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs (status);
