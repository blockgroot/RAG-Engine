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
