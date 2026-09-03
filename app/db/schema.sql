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

-- Incremental sync (source page id + last_edited): lets re-sync upsert only
-- changed pages instead of appending duplicates.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_external_id TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_last_modified TIMESTAMPTZ;

-- Sync state is partitioned per provider (Google Integration Phase 1): without
-- this, a Google sync in an org that also has Notion would compute
-- removed = every Notion page id and delete the whole Notion corpus. See
-- CLAUDE.md §4 and GOOGLE_INTEGRATION_PLAN.md §3.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_provider TEXT;
UPDATE documents SET source_provider = 'notion'
    WHERE source_provider IS NULL AND source_external_id IS NOT NULL;

DROP INDEX IF EXISTS idx_documents_org_external;
-- Superseded below (once workspace_id exists on documents) by two partial
-- unique indexes — see the Workspace-within-a-Workspace block further down.
DROP INDEX IF EXISTS idx_documents_org_provider_external;

-- Hard metadata filter (production-RAG comparison gap #3, department/tag
-- half — date-range shipped first via source_last_modified above). A
-- generic, source-agnostic tag list rather than a fixed "department" column:
-- nothing here decides WHERE a tag comes from (an admin-set field, a Notion
-- property, a folder mapping) — that is a product decision for whoever
-- calls the ingest/store functions with a value, mirroring how
-- source_provider/workspace_id are pure pass-through scoping with no
-- built-in population logic. NULL/empty (the default) never excludes a
-- document from an unfiltered query, matching every other optional filter
-- in this schema.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags TEXT[];

-- Who last edited the source document, as the source names them. Captured at
-- ingest so a "top editors" chart is a GROUP BY rather than an API call per
-- page load -- Drive returns it in the `files.list` we already make, and Notion
-- needs one cached `GET /users/{id}` per distinct person.
--
-- NOT backfillable: it was never captured before, so a chart grouped by editor
-- necessarily starts at the first sync after this shipped (which is why
-- `insights.store.first_fact_at` exists). Inventing a name would be worse than
-- starting late.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_last_editor TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_tags ON documents USING gin (tags);


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

-- Domain-based auto-join (org_domains: admin-typed domain + auto_join_enabled,
-- no DNS proof) was removed in favor of direct admin-invited members — see
-- CLAUDE.md §2/§4. Dropped rather than left unused since nothing reads it any
-- more; revive by restoring this table + app/auth/domains.py from git history
-- if/when self-serve multi-company onboarding is actually needed.
DROP TABLE IF EXISTS org_domains;

-- Application users (Phase 10). `org_id` is nullable only for the brief window
-- before an org is resolved (an admin's own org at signup, or a specific
-- email an admin invites directly); a user in that state is NEVER issued a
-- session (see app/api/auth.py) so a null-org row can't act on any tenant's
-- data. `role` gates admin-only endpoints.
CREATE TABLE IF NOT EXISTS users (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email      TEXT NOT NULL UNIQUE,
    org_id     UUID REFERENCES organizations (id) ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_org ON users (org_id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_revoked_at TIMESTAMPTZ;

-- `owner_email_whitelist` (pre-approved-list gate on POST /auth/signup) was
-- REPLACED by the signup-approval queue below — see CLAUDE.md §2/§4. A
-- static whitelist still required editing it out-of-band before every new
-- owner could sign up; the approval queue lets ANY email request an org and
-- gates creation on a human decision instead, so there's no list to
-- pre-populate. Dropped rather than left unused. Revive by restoring this
-- table + app/auth/owner_whitelist.py from git history if a pre-approved-list
-- gate is ever wanted again instead of (or alongside) review-based approval.
DROP TABLE IF EXISTS owner_email_whitelist;

-- Self-serve org creation request queue: a brand-new company's first user no
-- longer creates an org+admin synchronously at /auth/signup — they land here
-- as `pending` until the platform owner approves via the one-click email
-- links below (see CLAUDE.md §2/§4) — the ONLY review surface, no CLI or
-- admin UI. No session/login/cookie surface — the email links carry a
-- single-use possession token, same trust model as magic_link_tokens, not a
-- second auth system. This is a plain CREATE TABLE (no ALTER on an existing
-- table), so it carries none of the ALTER-ordering hazard documented near
-- `ingestion_jobs`/`workspace_id` below.
CREATE TABLE IF NOT EXISTS org_signup_requests (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL,
    company_name  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    reject_reason TEXT,
    org_id        UUID REFERENCES organizations (id) ON DELETE SET NULL,
    reviewed_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_org_signup_requests_status ON org_signup_requests (status);

-- One-click email approve/reject: two single-use possession tokens
-- generated together at request-creation time (only their SHA-256 hashes
-- are ever stored, like magic_link_tokens). No separate "consumed" flag is
-- needed — approve_signup_request/reject_signup_request already guard
-- atomically on status='pending', so the request's own status transition
-- IS the one-time-use gate; a token stays hash-verifiable after use, but
-- re-attempting the action is a no-op, not a double-action. This ALTER is
-- safe immediately after this table's own CREATE TABLE above (no ordering
-- hazard — see the gotcha in §4 about which schema changes DO have one).
ALTER TABLE org_signup_requests ADD COLUMN IF NOT EXISTS approve_token_hash TEXT;
ALTER TABLE org_signup_requests ADD COLUMN IF NOT EXISTS reject_token_hash TEXT;
ALTER TABLE org_signup_requests ADD COLUMN IF NOT EXISTS action_expires_at TIMESTAMPTZ;

-- One PENDING request per email at a time (partial unique index, same
-- pattern as idx_oauth_connections_org_provider_orgwide below): a second
-- signup attempt while one is already pending conflicts; re-submitting after
-- a rejection is allowed since a rejected row no longer matches this index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_org_signup_requests_email_pending
    ON org_signup_requests (email) WHERE status = 'pending';

-- Employee-created sub-workspaces (Workspace-within-a-Workspace). A
-- sub-workspace nests INSIDE its parent org — every row it owns still
-- carries org_id, so the org_id isolation proof stays a strict subset of
-- this new boundary rather than a second, parallel mechanism. `created_by`
-- is the employee who created it (always an org member, enforced at the
-- API layer, never a cross-org id).
-- created_by/invited_by below use ON DELETE SET NULL (not the default NO
-- ACTION) so that deleting an org cascades cleanly: organizations -> users
-- and organizations -> workspaces both cascade from the SAME org_id delete,
-- with no guaranteed ordering between them, so a plain NO ACTION reference
-- from workspace rows back to users can transiently violate the FK mid-
-- cascade. SET NULL sidesteps the ordering dependency entirely; the
-- workspace/membership row itself is still removed via its own org_id/
-- workspace_id cascade regardless.
CREATE TABLE IF NOT EXISTS workspaces (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_by UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_workspaces_org ON workspaces (org_id);
ALTER TABLE workspaces ALTER COLUMN created_by DROP NOT NULL;
ALTER TABLE workspaces DROP CONSTRAINT IF EXISTS workspaces_created_by_fkey;
ALTER TABLE workspaces ADD CONSTRAINT workspaces_created_by_fkey
    FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL;

-- Membership in a sub-workspace. A member must already belong to the same
-- org as the workspace (enforced in app/workspaces/, not by a DB constraint
-- alone, since that requires a cross-table check) — a sub-workspace can
-- never admit someone from a different tenant. `role` mirrors org roles
-- (owner = created it / can invite+connect sources, member = can only ask
-- questions).
CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id UUID NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT 'member',
    invited_by   UUID REFERENCES users (id) ON DELETE SET NULL,
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members (user_id);
ALTER TABLE workspace_members DROP CONSTRAINT IF EXISTS workspace_members_invited_by_fkey;
ALTER TABLE workspace_members ADD CONSTRAINT workspace_members_invited_by_fkey
    FOREIGN KEY (invited_by) REFERENCES users (id) ON DELETE SET NULL;

-- Workspace-within-a-Workspace: NULL = org-wide (unchanged default; every
-- existing row and every existing query path is unaffected), non-NULL =
-- scoped to that sub-workspace. Never query workspace_id alone — always
-- paired with org_id (see app/rag/scope.py).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_documents_workspace ON documents (workspace_id);

-- Provider-partitioned sync uniqueness (org_id, source_provider,
-- source_external_id) must ALSO be scoped by workspace_id, or the same
-- external file synced into a personal workspace collides with the org-wide
-- copy's row on INSERT. Same reasoning as oauth_connections above: Postgres
-- treats NULL as distinct-from-NULL in a plain multi-column UNIQUE, so a
-- naive UNIQUE(org_id, source_provider, source_external_id, workspace_id)
-- would let unlimited "duplicate" org-wide (workspace_id IS NULL) rows
-- through. Two partial unique indexes instead: one for the org-wide
-- connection, one for a specific workspace's connection.
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_org_provider_external_orgwide
    ON documents (org_id, source_provider, source_external_id)
    WHERE workspace_id IS NULL AND source_external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_org_provider_external_workspace
    ON documents (org_id, source_provider, source_external_id, workspace_id)
    WHERE workspace_id IS NOT NULL AND source_external_id IS NOT NULL;

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_chunks_workspace ON chunks (workspace_id);

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_conversations_workspace ON conversations (workspace_id);

ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;

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
    external_workspace_name TEXT,
    access_token_encrypted  TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    expires_at              TIMESTAMPTZ,
    connected_by_user_id    UUID REFERENCES users (id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, provider)
);
CREATE INDEX IF NOT EXISTS idx_oauth_connections_org ON oauth_connections (org_id);
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS external_workspace_name TEXT;

-- Workspace-within-a-Workspace: a personal connection (e.g. an employee's
-- own Notion "Meeting Notes" page) is scoped to their sub-workspace, not the
-- org. workspace_id NULL = today's org-wide admin connection (behavior
-- unchanged). The uniqueness must include workspace_id or a second member's
-- personal connection for the same provider would collide with (and via the
-- existing ON CONFLICT upsert, silently overwrite) an unrelated workspace's
-- connection or the org-wide one.
--
-- Two PARTIAL unique indexes, not one UNIQUE(org_id, provider, workspace_id):
-- Postgres treats NULL as distinct-from-NULL in a plain multi-column UNIQUE,
-- which would let unlimited org-wide (workspace_id IS NULL) rows through —
-- exactly the ambiguity this table exists to prevent. Splitting by
-- "IS NULL" / "IS NOT NULL" keeps "at most one org-wide row per provider"
-- exactly as before, while giving each real workspace_id its own row. Also
-- lets ON CONFLICT target the right index by matching its WHERE clause (see
-- app/auth/credentials.py::save_connection).
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
ALTER TABLE oauth_connections DROP CONSTRAINT IF EXISTS oauth_connections_org_id_provider_key;
-- DROP + recreate (not just IF NOT EXISTS) because an earlier iteration of
-- this schema briefly created a COALESCE-expression index under this same
-- name before landing on the partial-index form below; IF NOT EXISTS alone
-- would silently keep that stale definition on any DB that already applied
-- the earlier version, causing ON CONFLICT to fail to match this index.
DROP INDEX IF EXISTS idx_oauth_connections_org_provider_workspace;
CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_connections_org_provider_orgwide
    ON oauth_connections (org_id, provider) WHERE workspace_id IS NULL;
CREATE UNIQUE INDEX idx_oauth_connections_org_provider_workspace
    ON oauth_connections (org_id, provider, workspace_id) WHERE workspace_id IS NOT NULL;

-- Provider-specific ingestion scope config (Google Integration Phase 4). Unlike
-- Notion (which always ingests every page shared with the integration token),
-- some sources need the admin to designate an in-scope subset up front — e.g.
-- Google Drive requires a specific folder id, since un-scoped Drive access is
-- both a tenant-isolation risk and broader than Google's OAuth scope policy
-- allows. Deliberately generic (JSONB, no Google-specific columns) so a future
-- GitHub adapter's repo name or Slack adapter's channel list fits the same
-- column. Nullable with no default: most providers (Notion) never set it.
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS source_config JSONB;

-- Connection health: sticky "needs reconnect" so Sources can show a Reconnect
-- CTA after an admin leaves / token dies, without relying on in-memory React
-- state from the last failed change-check. Cleared on reconnect or a successful
-- live call; set when refresh or an upstream 401/unauthorized hits.
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS needs_reauth BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS reauth_reason TEXT;

-- Automatic sync (freshness without a human). Two columns, because there are
-- exactly two reasons to sync: a service TOLD us something changed
-- (``sync_requested_at``, stamped by a webhook handler) or enough time has
-- passed that we should look anyway (``last_sync_at`` + the configured
-- interval). The poll is the floor, not the plan: it covers Drive (whose push
-- notifications need a Google-verified domain we do not own) and any webhook
-- that was dropped while the free-tier box was asleep.
--
-- ``sync_requested_at`` is a FLAG, not a queue: a burst of fifty Slack
-- messages stamps it fifty times and produces one job, because the tick reads
-- it once and clears it. That is the whole debounce -- no timer, no counter.
-- The webhook handler must never ingest inline: Slack requires a 3-second ack.
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS sync_requested_at TIMESTAMPTZ;
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS last_sync_at TIMESTAMPTZ;

-- Partial: only rows actually waiting are scanned, and "waiting" is the
-- common-case empty set.
CREATE INDEX IF NOT EXISTS idx_oauth_connections_sync_requested
    ON oauth_connections (sync_requested_at)
    WHERE sync_requested_at IS NOT NULL;

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

-- Workspace-within-a-Workspace: which sub-workspace (if any) this job's
-- fetched content should be stored under (NULL = today's org-wide admin
-- job, unchanged). Must come AFTER this CREATE TABLE — on a fresh database
-- this ALTER would otherwise fail since the table wouldn't exist yet.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;

-- Live progress. Without these a job is binary (queued -> running -> succeeded)
-- and `doc_count` is only written at the very end, so every poll during a
-- multi-minute sync returned byte-identical JSON and the UI could only spin —
-- indistinguishable, to the person watching, from a hung sync. These columns
-- are what make the spinner truthful. Same ALTER-after-CREATE ordering rule as
-- `workspace_id` above.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS phase TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS total_documents INT;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS processed_documents INT NOT NULL DEFAULT 0;

-- Crash-loop breaker. `requeue_interrupted_running()` returns orphaned
-- `running` jobs to `queued` on worker start, so a sync survives a normal
-- restart. But with no attempt counter that behaviour is unbounded: a job that
-- kills its own process (OOM) is requeued on the next boot, claimed, and kills
-- it again — an infinite, unattended crash loop that burns the whole instance
-- and is independent of WHY the job died. Two live production incidents were
-- this loop, not the underlying bug. Counting attempts lets the requeue refuse
-- a job that has already taken the process down `INGEST_MAX_JOB_ATTEMPTS`
-- times, so a poison job fails loudly instead of looping forever. Same
-- ALTER-after-CREATE ordering rule as the columns above.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0;

-- Liveness heartbeat, written on every progress report.
--
-- `reap_stuck()` used to decide a job was dead from `started_at` alone, i.e.
-- from how long it had been running rather than from whether it was still doing
-- anything. So a healthy-but-slow ingest (a large folder, or contextualization
-- against a 15-rpm endpoint) was marked `failed` at the timeout while it carried
-- on working, and the UI reported a failure for a run that was fine. The
-- evidence of liveness already existed -- `update_progress` writes phase and
-- counters as each document lands -- it just had no timestamp for the reaper to
-- read. Same wrong-gauge mistake as measuring memory with `ru_maxrss`: the
-- number was real, it just wasn't the number that answers the question.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS progress_at TIMESTAMPTZ;

-- At most one queued/running job per connection. Without this, two parallel
-- POST /ingest calls can both pass has_active_job() and enqueue twice — the
-- second run often re-embeds the same pages and makes Update look "stuck".
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_jobs_one_active_per_connection
    ON ingestion_jobs (connection_id)
    WHERE status IN ('queued', 'running');

-- Single-use magic-link login tokens (Phase 13). Only a HASH of the token is
-- stored (never the token itself), so a DB read can't be used to log in as
-- someone. `consumed_at` makes a token single-use even if it leaks (e.g. in a
-- mail server log) before it expires; `expires_at` bounds its lifetime
-- regardless. A session is only ever issued from a verify call that
-- successfully consumes one of these rows (see app/api/auth.py).
CREATE TABLE IF NOT EXISTS magic_link_tokens (
    token_hash  TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_email ON magic_link_tokens (email);

-- Single-use OAuth `state` values (Phase 13) — CSRF/replay protection for the
-- admin "Connect X" flow. Stored server-side (not just a signed JWT) so a
-- state can be validated AND immediately consumed on lookup in the callback;
-- scoped to the admin's org_id at issue time so the callback resolves the
-- right tenant without trusting anything client-supplied.
CREATE TABLE IF NOT EXISTS oauth_states (
    state       TEXT PRIMARY KEY,
    org_id      UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Workspace-within-a-Workspace: when a "Connect" flow is for a personal
-- sub-workspace source rather than the org-wide one, the state row records
-- which workspace so the callback knows to save the resulting connection
-- scoped to it (NULL = today's org-wide connect flow, unchanged).
ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;

-- GitHub connect: after user OAuth, the callback lists every App installation
-- this user can see and parks the user token here so the frontend can prompt
-- "which account?" before we bind Company Sources vs a space. Single-use,
-- short TTL — same trust model as oauth_states / magic_link_tokens. Tokens
-- are Fernet-encrypted at rest; only hashes aren't needed because possession
-- of the random ``token`` is the capability (like oauth state).
CREATE TABLE IF NOT EXISTS github_install_pending (
    token                      TEXT PRIMARY KEY,
    org_id                     UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id               UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    access_token_encrypted     TEXT NOT NULL,
    refresh_token_encrypted    TEXT,
    token_expires_at           TIMESTAMPTZ,
    expires_at                 TIMESTAMPTZ NOT NULL,
    consumed_at                TIMESTAMPTZ,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_github_install_pending_expires
    ON github_install_pending (expires_at);


-- Short-TTL cache for repeated standalone policy questions (Phase 19).
CREATE TABLE IF NOT EXISTS query_answer_cache (
    org_id               UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    question_hash        TEXT NOT NULL,
    normalized_question  TEXT NOT NULL,
    payload              JSONB NOT NULL,
    expires_at           TIMESTAMPTZ NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, question_hash)
);
CREATE INDEX IF NOT EXISTS idx_query_answer_cache_expires ON query_answer_cache (expires_at);

-- Fixed-window HTTP rate counters (Phase 21).
CREATE TABLE IF NOT EXISTS api_rate_counters (
    scope_key      TEXT NOT NULL,
    window_start   TIMESTAMPTZ NOT NULL,
    request_count  INT NOT NULL DEFAULT 0,
    PRIMARY KEY (scope_key, window_start)
);

-- Prompt-Driven Activity Scheduler (Phase 1, org scope): a user-authored
-- recurring question against one already-connected service. The row IS the
-- queue entry (definition + run state combined, same shape as
-- ingestion_jobs) — no separate run-history table for Phase 1 (YAGNI; add
-- one if per-run history is ever needed). Any org member may create one
-- against any org-wide connection; list/edit/delete are scoped to the
-- owning user_id, not the whole org, per "see/edit/remove their OWN
-- schedulers". Phase 1 supports github/slack only — provider is unchecked
-- here (same discipline as ingestion_jobs.status: validated by the
-- application, not a CHECK constraint) so adding a source later needs no
-- migration.
-- NOTE: the ONLY tenant table scoped by (org_id, user_id) — a scheduler is
-- personal, so every read/write must filter on BOTH. Do not copy this shape
-- for shared org data; every other table here is org_id (+ workspace_id) only.
CREATE TABLE IF NOT EXISTS schedulers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id        UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES oauth_connections (id) ON DELETE CASCADE,
    provider      TEXT NOT NULL,
    frequency     TEXT NOT NULL,           -- 'weekly' | 'monthly'
    prompt        TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',  -- active | failed
    last_run_at   TIMESTAMPTZ,
    next_run_at   TIMESTAMPTZ NOT NULL,
    attempts      INT NOT NULL DEFAULT 0,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_schedulers_org ON schedulers (org_id);
CREATE INDEX IF NOT EXISTS idx_schedulers_user ON schedulers (user_id);
CREATE INDEX IF NOT EXISTS idx_schedulers_due ON schedulers (next_run_at) WHERE status = 'active';

-- Workspace-within-a-Workspace: a scheduler may target either the org-wide
-- connection (NULL, the default) or one sub-workspace's own connection. Nested
-- INSIDE org_id like every other workspace_id here — never valid alone. The
-- fetchers already take workspace_id; this column is what lets a saved
-- scheduler remember which scope it was created for.
ALTER TABLE schedulers ADD COLUMN IF NOT EXISTS workspace_id UUID
    REFERENCES workspaces (id) ON DELETE CASCADE;

-- NOTE: a `schedulers.model` column briefly existed (per-scheduler model
-- choice) and was removed — reports always run on the configured model, since
-- a scheduled run is unattended. Databases migrated during that window keep an
-- unused nullable column; harmless, and not worth a destructive migration.

-- One generated report, kept so the email can be a short "it's ready" note
-- plus a link instead of a wall of plain text, and so a report survives a
-- failed send (the run's work is no longer lost with the mail).
--
-- Deliberately snapshotted, not joined: ``prompt``, ``provider``, ``frequency``
-- and ``space_name`` are copied at generation time. A report is an immutable
-- record of what was asked and what came back — re-resolving them later would
-- silently rewrite history when someone edits their scheduler or renames a
-- space, and would delete archived reports when a space is deleted.
CREATE TABLE IF NOT EXISTS scheduler_reports (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scheduler_id UUID REFERENCES schedulers (id) ON DELETE CASCADE,
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,
    frequency    TEXT NOT NULL,
    prompt       TEXT NOT NULL,
    space_name   TEXT,                     -- NULL = company-wide
    report_text  TEXT NOT NULL,
    items        JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes        JSONB NOT NULL DEFAULT '[]'::jsonb,
    window_start TIMESTAMPTZ NOT NULL,
    window_end   TIMESTAMPTZ NOT NULL,
    delivered_to TEXT,                     -- NULL until the mail is accepted
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- NOTE: a `scheduler_reports.model` column briefly existed alongside
-- `schedulers.model` and was removed with it. Same story as above.
-- Every read is "this person's reports, newest first".
CREATE INDEX IF NOT EXISTS idx_scheduler_reports_owner
    ON scheduler_reports (org_id, user_id, created_at DESC);

-- One countable event from a connected source. The ONLY numeric substrate for
-- the visualizations section: every chart in the product is a GROUP BY over
-- this table, so no chart can ever contain a number an LLM invented. A prose
-- answer that is wrong hedges and cites; a bar chart that is wrong reads as a
-- measurement, and neither the confidence gate nor the strict prompt can check
-- arithmetic.
--
-- Deliberately narrow and denormalized. A wide per-provider table (pr_author,
-- issue_assignee, message_channel) makes every cross-provider chart a join
-- somebody has to write; one actor/subject/state triple makes it a WHERE
-- clause. `value` carries the one number a fact may have (a lead time in
-- seconds, a sentiment score) and is NULL for pure count facts.
--
-- Scoped like every other tenant table: org_id always, workspace_id nested
-- inside it (NULL = org-wide).
--
-- GitHub DOES write rows here, and that is not a break with "GitHub embeds
-- nothing": that rule is about vectors -- no documents, no chunks, no
-- embeddings, no SourceAdapter -- and a counter is not a chunk. Facts are
-- written by a facts-only sync path that never enqueues an ingestion job, so
-- "Unknown source type: 'github'" stays impossible.
CREATE TABLE IF NOT EXISTS activity_facts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,          -- notion | google | linear | slack | github | forms
    kind         TEXT NOT NULL,          -- doc_changed | pr_opened | issue_completed | ...
    actor        TEXT,                   -- the person, when the source tells us
    subject      TEXT,                   -- repo | channel | team | doc title
    state        TEXT,                   -- issue state, sentiment label
    occurred_at  TIMESTAMPTZ NOT NULL,
    value        NUMERIC,                -- lead-time seconds, score; NULL = count-only
    url          TEXT,
    external_id  TEXT,                   -- for idempotent re-ingest, see below
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every read is "this scope, this provider, this window" -- the indexes mirror
-- that exactly rather than being general-purpose.
CREATE INDEX IF NOT EXISTS idx_activity_facts_scope
    ON activity_facts (org_id, provider, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_activity_facts_space
    ON activity_facts (org_id, workspace_id, provider, occurred_at DESC);

-- Re-syncing the same document or pull request must not double every count.
-- Partial unique indexes because NULL workspace_id means "org-wide" and
-- Postgres treats NULLs as distinct in a plain UNIQUE -- the same trap
-- oauth_connections already hit. A NULL external_id is exempt: a fact the
-- source gave us no stable id for cannot be deduplicated, and pretending
-- otherwise would collapse every such fact onto one row.
CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_facts_org
    ON activity_facts (org_id, provider, kind, external_id)
    WHERE workspace_id IS NULL AND external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_facts_space
    ON activity_facts (org_id, workspace_id, provider, kind, external_id)
    WHERE workspace_id IS NOT NULL AND external_id IS NOT NULL;

-- A chart a member asked for and kept. Personal, scoped `(org_id, user_id)`
-- like `schedulers` and unlike every other tenant table -- a pin is one
-- person's shortcut, never published to anyone, which is why this feature has
-- no sharing model and no approval step.
--
-- Stores the SPEC, not the numbers: re-running it is one GROUP BY, and
-- snapshotting counts would freeze a chart that is supposed to stay current
-- (the opposite of `scheduler_reports`, which snapshots precisely because a
-- report is a record of one moment).
CREATE TABLE IF NOT EXISTS insight_pins (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    metric       TEXT NOT NULL,
    group_by     TEXT,
    period       TEXT NOT NULL,
    title        TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_insight_pins_owner
    ON insight_pins (org_id, user_id, created_at DESC);

-- Pinning the same chart twice is a no-op, not a duplicate. Partial indexes
-- because NULL workspace_id means "the company" and Postgres treats NULLs as
-- distinct in a plain UNIQUE.
CREATE UNIQUE INDEX IF NOT EXISTS uq_insight_pins_org
    ON insight_pins (org_id, user_id, metric, period, coalesce(group_by, ''))
    WHERE workspace_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_insight_pins_space
    ON insight_pins (org_id, user_id, workspace_id, metric, period,
                     coalesce(group_by, ''))
    WHERE workspace_id IS NOT NULL;
