-- Handbook database schema (Postgres + pgvector). Multi-tenant: every tenant
-- table carries org_id (tests/test_isolation.py). Idempotent: safe to rerun.
--
-- The embedding dimension (1024) must match the embedding model and
-- DatabaseSettings.embedding_dim. Embeddings are `halfvec` (half the table, a
-- third of the HNSW index); bind or cast a query vector as halfvec too, or the
-- index is skipped.

CREATE EXTENSION IF NOT EXISTS vector;

-- Tenants. Everything else hangs off an organization.
CREATE TABLE IF NOT EXISTS organizations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The company's email domain ("syvora.com"), from its first admin's address.
-- Invites and email changes must use it (auth/users.assert_company_email).
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS email_domain TEXT;

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

-- Sync state is per provider: otherwise a Google sync would see every Notion
-- page as removed and delete it.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_provider TEXT;
UPDATE documents SET source_provider = 'notion'
    WHERE source_provider IS NULL AND source_external_id IS NOT NULL;

DROP INDEX IF EXISTS idx_documents_org_external;
-- Superseded below (once workspace_id exists on documents) by two partial
-- unique indexes — see the Workspace-within-a-Workspace block further down.
DROP INDEX IF EXISTS idx_documents_org_provider_external;

-- Generic tag filter (e.g. `slack:channel:<id>`); callers decide where tags
-- come from. NULL/empty never excludes a document from an unfiltered query.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tags TEXT[];

-- Backfill: tag each indexed Slack thread with its channel (the first half of
-- its `<channel>:<thread_ts>` id; no API call). Idempotent.
UPDATE documents
   SET tags = coalesce(tags, '{}') || ('slack:channel:' || split_part(source_external_id, ':', 1))
 WHERE source_provider = 'slack'
   AND source_external_id IS NOT NULL
   AND position(':' in source_external_id) > 1
   AND NOT coalesce(tags, '{}') && ARRAY['slack:channel:' || split_part(source_external_id, ':', 1)];

-- Who last edited the source document, captured at ingest so "top editors" is
-- a GROUP BY. Not backfillable, so editor charts start at the first sync after
-- it shipped (`insights.store.first_fact_at`).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_last_editor TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_tags ON documents USING gin (tags);

-- Document-level access, captured at sync from the source's sharing.
-- `doc_is_public` defaults TRUE (old rows and providers with no ACL, e.g.
-- Notion, keep "readable by the indexing scope"). `doc_viewers` holds ACL
-- entries (lowercased emails, `domain:<host>`, `channel:<id>`), not user ids,
-- since files are shared before people sign up; ignored when public.
-- Fail-closed happens at write time (app/ingestion/pipeline.py): no readable
-- sharing means the connected account only.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_is_public BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS doc_viewers TEXT[];
-- GIN, because the retrieval predicate is an array OVERLAP (`&&`) evaluated
-- per candidate row inside the same WHERE clause that already pins org_id.
CREATE INDEX IF NOT EXISTS idx_documents_viewers ON documents USING gin (doc_viewers);

-- Backfill: a PRIVATE channel's threads carry `channel:<id>`, so a reply in
-- that channel (public_only plus the channel) can quote them. From the
-- external id, no API call; private rows only; idempotent.
UPDATE documents
   SET doc_viewers = coalesce(doc_viewers, '{}') || ('channel:' || split_part(source_external_id, ':', 1))
 WHERE source_provider = 'slack'
   AND doc_is_public = FALSE
   AND position(':' in source_external_id) > 1
   AND NOT coalesce(doc_viewers, '{}') && ARRAY['channel:' || split_part(source_external_id, ':', 1)];

-- Knowledge-graph metadata an adapter saw while fetching (`sources.meta`), at
-- no extra API cost; the graph is rebuilt only from these rows. NULL = not
-- captured yet. `source_editor_key` is the editor's identity (`slack:U123`,
-- `email:ada@x.com`), never a display name.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_meta JSONB;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_editor_key TEXT;


-- A chunks table from before per-org partitioning is moved aside here and
-- copied into the partitioned table at the end of this file.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE oid = to_regclass('chunks') AND relkind = 'r') THEN
        ALTER TABLE chunks RENAME TO chunks_unpartitioned;
        ALTER INDEX chunks_pkey RENAME TO chunks_unpartitioned_pkey;
        DROP INDEX IF EXISTS idx_chunks_embedding, idx_chunks_content_tsv,
                             idx_chunks_workspace;
    END IF;
END $$;

-- Chunks of a document with their embedding. org_id is denormalized for the
-- tenant filter. One partition per organization (end of this file), so a
-- search walks only that org's vectors.
CREATE TABLE IF NOT EXISTS chunks (
    id          UUID NOT NULL DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index INT  NOT NULL,
    content     TEXT NOT NULL,
    embedding   halfvec(1024) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, id)
) PARTITION BY LIST (org_id);

-- No org_id index: within a partition every row has the same org_id, and
-- given one the planner preferred it to the vector index below.

-- `idx_chunks_embedding` is created after the halfvec conversion below:
-- Postgres checks the operator class before IF NOT EXISTS.

-- Generated full-text column for hybrid retrieval, fused with vector search by
-- RRF (app/rag/retrieval.py).
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED;
CREATE INDEX IF NOT EXISTS idx_chunks_content_tsv ON chunks USING gin (content_tsv);

-- Prompt-injection score (app/guard/): the RAW probability (threshold changes
-- need no rescan) and the scoring model. NULL = unscored, never clean or flagged.
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS injection_score REAL;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS injection_model TEXT;

-- Conversations: turns grouped for follow-ups; `summary` is the running
-- compression of older turns.
CREATE TABLE IF NOT EXISTS conversations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    summary    TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_conversations_org ON conversations (org_id);

-- One question and its answer in a conversation (org_id denormalized).
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

-- Last turn's retrieved chunks, one row per conversation, as JSON. The pipeline
-- reuses them when the next question is close enough (no LLM call). No vectors
-- stored; recomputed from `content`. Cascades with its conversation.
CREATE TABLE IF NOT EXISTS conversation_last_retrieval (
    conversation_id UUID PRIMARY KEY REFERENCES conversations (id) ON DELETE CASCADE,
    org_id          UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    chunks          TEXT NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Domain auto-join (org_domains) was removed in favour of explicit invites.
DROP TABLE IF EXISTS org_domains;

-- Application users. A NULL org_id user is never issued a session. `role`
-- gates admin-only endpoints.
CREATE TABLE IF NOT EXISTS users (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email      TEXT NOT NULL UNIQUE,
    org_id     UUID REFERENCES organizations (id) ON DELETE CASCADE,
    role       TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_org ON users (org_id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS sessions_revoked_at TIMESTAMPTZ;
-- What the person asked to be called. NULL until they say: a name guessed from
-- the email ("sana.asiwal" -> "Sana") was wrong for many addresses.
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT;

-- `owner_email_whitelist` was replaced by the signup-approval queue below.
DROP TABLE IF EXISTS owner_email_whitelist;

-- Org-creation request queue: a new company's first user waits as `pending`
-- until the platform owner approves from the email (single-use possession
-- tokens, the only review surface).
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

-- Approve/reject tokens, stored as SHA-256 hashes only. The status guard
-- (status='pending') is the one-time-use gate.
ALTER TABLE org_signup_requests ADD COLUMN IF NOT EXISTS approve_token_hash TEXT;
ALTER TABLE org_signup_requests ADD COLUMN IF NOT EXISTS reject_token_hash TEXT;
ALTER TABLE org_signup_requests ADD COLUMN IF NOT EXISTS action_expires_at TIMESTAMPTZ;

-- One PENDING request per email; re-submitting after a rejection is allowed.
CREATE UNIQUE INDEX IF NOT EXISTS idx_org_signup_requests_email_pending
    ON org_signup_requests (email) WHERE status = 'pending';

-- Spaces (sub-workspaces) nest inside their org: every row still carries
-- org_id. created_by/invited_by are ON DELETE SET NULL because org delete
-- cascades to users and workspaces in no fixed order.
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

-- Space membership. Same-org membership is enforced in app/workspaces/.
-- owner = can invite and connect sources; member = can ask.
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

-- NULL = org-wide, else that space. Never query workspace_id alone; always
-- with org_id (app/rag/scope.py).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_documents_workspace ON documents (workspace_id);

-- Sync uniqueness must include workspace_id. NULLs are distinct in a plain
-- UNIQUE, so two partial unique indexes: org-wide and per space.
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

-- A conversation is PERSONAL. NULL = a row from before this column: still
-- resumable in scope (no owner can be invented).
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users (id) ON DELETE CASCADE;

-- How far `summary` has folded, as a `turn_index`; NULL = nothing yet.
-- Folded turns are marked, not deleted, so chat history stays complete;
-- `get_context` bounds what the model sees on its own.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS folded_through INT;

-- A title the person typed (NULL = the first question, as before) and when
-- they pinned the chat to the top of their list (NULL = not pinned).
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations (org_id, user_id);

ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;


-- The sources shown under the answer (links already checked http(s)). NULL
-- before this column.
ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS cited JSONB;

-- The chart as drawn: a SNAPSHOT, so a reopened chart never disagrees with the
-- words beside it. NULL without a chart.
ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS chart JSONB;

-- Who answered: the provenance pill's inputs (source, agent, tools, files,
-- live reads, model, passage count). NULL before this column.
ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS meta JSONB;

-- Per-org, per-provider OAuth credentials, encrypted at rest
-- (app/security/crypto.py). Every lookup filters by org_id.
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

-- workspace_id NULL = org-wide, else a space's connection. Two partial unique
-- indexes, since NULLs are distinct in a plain UNIQUE; ON CONFLICT targets one
-- by its WHERE (app/auth/credentials.py::save_connection).
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
ALTER TABLE oauth_connections DROP CONSTRAINT IF EXISTS oauth_connections_org_id_provider_key;
-- DROP + recreate: an earlier COALESCE index had this name, and IF NOT EXISTS
-- would keep it and break ON CONFLICT.
DROP INDEX IF EXISTS idx_oauth_connections_org_provider_workspace;
CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_connections_org_provider_orgwide
    ON oauth_connections (org_id, provider) WHERE workspace_id IS NULL;
CREATE UNIQUE INDEX idx_oauth_connections_org_provider_workspace
    ON oauth_connections (org_id, provider, workspace_id) WHERE workspace_id IS NOT NULL;

-- Provider-specific scope config (JSONB): Drive folder, Slack channels,
-- Linear teams, Forms ids. NULL for providers that need none (Notion).
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS source_config JSONB;

-- Sticky "needs reconnect" for the Sources card. Set on a failed refresh or an
-- upstream 401; cleared on reconnect or a successful live call.
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS needs_reauth BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS reauth_reason TEXT;

-- Automatic sync: a service flagged a change (`sync_requested_at`, from a
-- webhook) or the interval passed (`last_sync_at`). The poll is the floor
-- (Drive, and pushes lost while the box slept). The flag is not a queue: fifty
-- pushes make one job. Webhooks never ingest inline.
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS sync_requested_at TIMESTAMPTZ;
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS last_sync_at TIMESTAMPTZ;

-- Drive push: one `changes.watch` channel per Google connection, re-watched
-- before `expires_at` (channels expire within a week). `token_hash` is the
-- SHA-256 of the channel token; a notification that does not match is ignored.
CREATE TABLE IF NOT EXISTS drive_watch_channels (
    connection_id UUID PRIMARY KEY REFERENCES oauth_connections (id) ON DELETE CASCADE,
    channel_id    TEXT NOT NULL UNIQUE,
    resource_id   TEXT NOT NULL,
    token_hash    TEXT NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partial: only rows actually waiting are scanned, and "waiting" is the
-- common-case empty set.
CREATE INDEX IF NOT EXISTS idx_oauth_connections_sync_requested
    ON oauth_connections (sync_requested_at)
    WHERE sync_requested_at IS NOT NULL;

-- Ingestion jobs: a durable, pollable record drained by a Postgres-backed
-- worker (app/jobs/), so a crashed worker never loses a job.
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

-- The space the fetched content belongs to (NULL = org-wide). After the CREATE
-- TABLE, or a fresh database fails.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;

-- Live progress, so polling shows a moving sync instead of a spinner that
-- looks hung. ALTER after CREATE, as above.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS phase TEXT;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS total_documents INT;
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS processed_documents INT NOT NULL DEFAULT 0;

-- Crash-loop breaker: orphaned `running` jobs are requeued on worker start,
-- but a job that kills its process (OOM) would loop forever. After
-- `INGEST_MAX_JOB_ATTEMPTS` it fails loudly instead.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0;

-- Liveness heartbeat, written on every progress report: `reap_stuck()` judges
-- a job dead by silence, not by how long it has run.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS progress_at TIMESTAMPTZ;

-- Files this run could not read the SHARING of. On the job, not the
-- connection, so the notification (latest job only) clears itself on a later 0.
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS permission_unreadable_documents INT NOT NULL DEFAULT 0;

-- At most one queued/running job per connection (two parallel POST /ingest
-- calls both passed has_active_job()).
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_jobs_one_active_per_connection
    ON ingestion_jobs (connection_id)
    WHERE status IN ('queued', 'running');

-- Single-use magic-link tokens: only a hash is stored; `consumed_at` keeps a
-- leaked token single-use, `expires_at` bounds it. Sessions come only from a
-- successful consume.
CREATE TABLE IF NOT EXISTS magic_link_tokens (
    token_hash  TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_magic_link_tokens_email ON magic_link_tokens (email);

-- A member's PRIOR sign-in addresses (`auth/email_change.py`), so grants to an
-- old address keep matching. One owner per address; every row was proven.
-- An alias another `users` row signs in with is ignored on read.
CREATE TABLE IF NOT EXISTS user_email_aliases (
    email      TEXT PRIMARY KEY,
    user_id    UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_email_aliases_user ON user_email_aliases (user_id);

-- A pending email change: a single-use token mailed to the NEW address. Apart
-- from magic_link_tokens so it can never log anyone in; hashed; `user_id`
-- bound from the session.
CREATE TABLE IF NOT EXISTS email_change_requests (
    token_hash  TEXT PRIMARY KEY,
    user_id     UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    new_email   TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_email_change_requests_user ON email_change_requests (user_id);

-- Single-use OAuth `state` (CSRF/replay), consumed on lookup and scoped to the
-- admin's org_id at issue, so the callback trusts nothing from the client.
CREATE TABLE IF NOT EXISTS oauth_states (
    state       TEXT PRIMARY KEY,
    org_id      UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    provider    TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The space a connect flow is for (NULL = org-wide).
ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
-- The PERSON who started a person-bound flow ("Link your GitHub account");
-- NULL for connect flows. Read from here, never the request.
ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users (id) ON DELETE CASCADE;

-- GitHub connect: the user token parked while they choose which installation
-- to bind. Single-use, short TTL, Fernet-encrypted; holding the random
-- ``token`` is the capability.
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


-- The old answer cache. Only standalone (Slack) questions ever used it, for
-- five minutes; removed, so Redis holds rate limits only.
DROP TABLE IF EXISTS query_answer_cache;

-- The old Postgres rate-limit counters. Rate limits are token buckets in
-- Redis now (app/ratelimit); the counters held nothing worth keeping.
DROP TABLE IF EXISTS api_rate_counters;

-- Recurring activity reports: a member's question against one connected
-- service. The row is also the queue entry (definition + run state).
-- Provider is validated by the app, not a CHECK. Personal: every read and
-- write filters on BOTH org_id and user_id.
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

-- The scope a scheduler targets: NULL = org-wide, else a space's connection
-- (always with org_id).
ALTER TABLE schedulers ADD COLUMN IF NOT EXISTS workspace_id UUID
    REFERENCES workspaces (id) ON DELETE CASCADE;

-- A `schedulers.model` column briefly existed and was removed (reports run on
-- the configured model); old databases keep an unused nullable column.

-- One generated report, so the email is a short "ready" note and a failed send
-- loses nothing. Labels are SNAPSHOTTED: a report is an immutable record, not
-- rewritten when a scheduler or space changes.
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

-- `scheduler_reports.model` was removed with `schedulers.model`.
-- Every read is "this person's reports, newest first".
CREATE INDEX IF NOT EXISTS idx_scheduler_reports_owner
    ON scheduler_reports (org_id, user_id, created_at DESC);

-- One countable event from a connected source: every activity chart is a
-- GROUP BY here, so no chart holds a model-invented number. Narrow and
-- denormalized (actor/subject/state) so cross-provider charts are a WHERE.
-- `value` is the one number a fact may carry (NULL for counts). Scoped by
-- org_id, workspace_id nested. GitHub writes facts here through a facts-only
-- path; "GitHub embeds nothing" is about vectors.
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

-- Re-syncing must not double counts. Partial unique indexes (NULL
-- workspace_id = org-wide); a NULL external_id cannot be deduplicated.
CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_facts_org
    ON activity_facts (org_id, provider, kind, external_id)
    WHERE workspace_id IS NULL AND external_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_activity_facts_space
    ON activity_facts (org_id, workspace_id, provider, kind, external_id)
    WHERE workspace_id IS NOT NULL AND external_id IS NOT NULL;

-- Stable identity of `actor` (`github:<login>`, `slack:U123`) for the graph,
-- which never matches on names. NULL where the source gave none.
ALTER TABLE activity_facts ADD COLUMN IF NOT EXISTS actor_key TEXT;

-- Every other simple field the source returned (labels, priority, project...),
-- so a chart can group, filter or sum by it with no new column. The chartable
-- set is discovered (`insights/attr_catalog.py`). `{}` = nothing captured yet.
ALTER TABLE activity_facts ADD COLUMN IF NOT EXISTS attrs JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Tables found inside documents, kept as typed rows so charts sum with SQL
-- (app/doctables). Cascades with `documents`, so access is the document's
-- (`visibility_predicate` on the JOIN). `columns` is the profile
-- `[{key: "c0", name, type, unit, ...}]`; only `key` is ever spliced into SQL.
CREATE TABLE IF NOT EXISTS doc_tables (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    document_id  UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    position     INT NOT NULL,
    name         TEXT NOT NULL,
    columns      JSONB NOT NULL,
    row_count    INT NOT NULL,
    truncated    BOOLEAN NOT NULL DEFAULT FALSE,
    notes        TEXT[] NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_doc_tables_scope ON doc_tables (org_id, workspace_id);

-- Which adapter wrote it (`app/doctables/base.py`): `table` (exact) or `text`
-- (figures read from sentences, kept only when the quote holds every cell).
-- Each adapter replaces only its own tables.
ALTER TABLE doc_tables ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'table';
ALTER TABLE doc_tables DROP CONSTRAINT IF EXISTS doc_tables_document_id_position_key;
CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_tables_origin
    ON doc_tables (document_id, origin, position);

-- One row per table row: `cells` normalized by column key (unparseable cells
-- absent), `raw` as the document wrote it.
CREATE TABLE IF NOT EXISTS doc_table_rows (
    table_id UUID NOT NULL REFERENCES doc_tables (id) ON DELETE CASCADE,
    row_no   INT NOT NULL,
    cells    JSONB NOT NULL,
    raw      JSONB NOT NULL,
    PRIMARY KEY (table_id, row_no)
);
-- The exact sentence a `text` row was read from, shown on hover so a reader
-- can check the figure against the document. NULL for a real table's rows.
ALTER TABLE doc_table_rows ADD COLUMN IF NOT EXISTS quote TEXT;
-- Rows named by a typed cell ("L-1008") are found through this index
-- (`store.rows_naming`), never a scan.
CREATE INDEX IF NOT EXISTS doc_table_rows_cells_lower
    ON doc_table_rows USING gin ((lower(raw::text)::jsonb));

-- Was the background queue for prose figures; prose is now read inline at
-- ingest, so this is only a read marker (`done`). Cascades with the document.
CREATE TABLE IF NOT EXISTS doc_text_queue (
    document_id  UUID PRIMARY KEY REFERENCES documents (id) ON DELETE CASCADE,
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    origin       TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    title        TEXT NOT NULL,
    text         TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',   -- done (read, no series) | unreadable (retried)
    attempts     INT NOT NULL DEFAULT 0,
    claimed_at   TIMESTAMPTZ,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_doc_text_queue_pending
    ON doc_text_queue (updated_at) WHERE status = 'pending';

-- When the dataset adapters last read a document. NULL = never (indexed
-- before charts read tables); `backfill_tables` reads a bounded batch per sync.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tables_checked_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_documents_tables_unchecked
    ON documents (org_id, source_provider) WHERE tables_checked_at IS NULL;

-- Releases this server has already emailed its admins about
-- (app/updates/announce.py). Not tenant data.
CREATE TABLE IF NOT EXISTS update_notices (
    version     TEXT PRIMARY KEY,
    notified_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One-time data fixes: this file reruns on every deploy, so each fix is
-- guarded by its own row here.
CREATE TABLE IF NOT EXISTS schema_marks (
    name       TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Companies made before email_domain existed take their earliest admin's domain.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'org_email_domain_backfill') THEN
        UPDATE organizations o SET email_domain = (
            SELECT lower(split_part(u.email, '@', 2)) FROM users u
             WHERE u.org_id = o.id AND u.role = 'admin'
             ORDER BY u.created_at LIMIT 1)
         WHERE o.email_domain IS NULL;
        INSERT INTO schema_marks (name) VALUES ('org_email_domain_backfill');
    END IF;
END $$;

-- Notion tables (no header separator) and inline databases were not read
-- before 2026-10; unstamp those pages so `backfill_tables` re-reads them. The
-- mark is named for the last fix it covers.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'notion_tables_recheck_databases') THEN
        UPDATE documents d SET tables_checked_at = NULL
         WHERE d.source_provider = 'notion'
           AND d.tables_checked_at IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM doc_tables t WHERE t.document_id = d.id);
        INSERT INTO schema_marks (name) VALUES ('notion_tables_recheck_databases');
    END IF;
END $$;

-- Prose figures were read only by a queue that was off by default; unstamp
-- prose pages never read so `backfill_tables` reads them inline. The old
-- queue's unfinished rows are dropped.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'prose_figures_read_at_ingest') THEN
        UPDATE documents d SET tables_checked_at = NULL
         WHERE d.source_provider IN ('notion', 'google', 'slack', 'linear')
           AND d.tables_checked_at IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM doc_tables t
                            WHERE t.document_id = d.id AND t.origin = 'text')
           AND NOT EXISTS (SELECT 1 FROM doc_text_queue q
                            WHERE q.document_id = d.id AND q.status = 'done');
        DELETE FROM doc_text_queue WHERE status <> 'done';
        INSERT INTO schema_marks (name) VALUES ('prose_figures_read_at_ingest');
    END IF;
END $$;

-- Once more, for pages deploys in between still stamped checked.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'prose_figures_inline_recheck') THEN
        UPDATE documents d SET tables_checked_at = NULL
         WHERE d.tables_checked_at IS NOT NULL
           AND EXISTS (SELECT 1 FROM doc_text_queue q
                        WHERE q.document_id = d.id AND q.status <> 'done')
           AND NOT EXISTS (SELECT 1 FROM doc_tables t
                            WHERE t.document_id = d.id AND t.origin = 'text');
        DELETE FROM doc_text_queue WHERE status <> 'done';
        INSERT INTO schema_marks (name) VALUES ('prose_figures_inline_recheck');
    END IF;
END $$;

-- Text tables read before code built one table per measure, unit, breakdown
-- and period (the model mixed measures): read once more.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'prose_figures_as_facts') THEN
        UPDATE documents d SET tables_checked_at = NULL
         WHERE EXISTS (SELECT 1 FROM doc_tables t
                        WHERE t.document_id = d.id AND t.origin = 'text');
        INSERT INTO schema_marks (name) VALUES ('prose_figures_as_facts');
    END IF;
END $$;

-- An unparseable reply was marked `done` for good; now retried, so pages
-- marked done with no figures are read once more.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'prose_done_reread') THEN
        UPDATE documents d SET tables_checked_at = NULL
         WHERE EXISTS (SELECT 1 FROM doc_text_queue q
                        WHERE q.document_id = d.id AND q.status = 'done')
           AND NOT EXISTS (SELECT 1 FROM doc_tables t
                            WHERE t.document_id = d.id AND t.origin = 'text');
        DELETE FROM doc_text_queue q WHERE q.status = 'done'
           AND NOT EXISTS (SELECT 1 FROM doc_tables t
                            WHERE t.document_id = q.document_id AND t.origin = 'text');
        INSERT INTO schema_marks (name) VALUES ('prose_done_reread');
    END IF;
END $$;

-- Category columns kept five samples until 2026-11 ("Won" fell off a Stage
-- column). New tables keep twelve (`extract.CATEGORY_SAMPLES`); stored ones are
-- re-sampled from their rows here, in Python's sorted() order.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'category_samples_twelve') THEN
        -- category_samples_twelve:begin
        UPDATE doc_tables t
           SET columns = (
               SELECT jsonb_agg(
                        CASE WHEN e.col->>'type' = 'category'
                              AND jsonb_array_length(COALESCE(e.col->'samples', '[]'::jsonb))
                                  < LEAST(COALESCE((e.col->>'distinct')::int, 0), 12)
                        THEN jsonb_set(e.col, '{samples}', COALESCE((
                               SELECT jsonb_agg(v ORDER BY v COLLATE "C")
                                 FROM (SELECT d.v FROM (
                                         SELECT DISTINCT r.cells->>(e.col->>'key') AS v
                                           FROM doc_table_rows r
                                          WHERE r.table_id = t.id
                                            AND r.cells ? (e.col->>'key')) d
                                        ORDER BY d.v COLLATE "C"
                                        LIMIT 12) s), e.col->'samples'))
                        ELSE e.col END
                      ORDER BY e.ord)
                 FROM jsonb_array_elements(t.columns) WITH ORDINALITY AS e(col, ord))
         WHERE EXISTS (
               SELECT 1 FROM jsonb_array_elements(t.columns) c
                WHERE c->>'type' = 'category'
                  AND jsonb_array_length(COALESCE(c->'samples', '[]'::jsonb))
                      < LEAST(COALESCE((c->>'distinct')::int, 0), 12));
        -- category_samples_twelve:end
        INSERT INTO schema_marks (name) VALUES ('category_samples_twelve');
    END IF;
    -- A date column's first and last day, for tables stored before
    -- `extract.Column.first/last`: which table holds "November" is read off them.
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'date_column_span') THEN
        -- date_column_span:begin
        UPDATE doc_tables t
           SET columns = (
               SELECT jsonb_agg(
                        CASE WHEN e.col->>'type' = 'date' AND NOT e.col ? 'first'
                        THEN COALESCE((
                               SELECT e.col || jsonb_build_object(
                                        'first', min(left(r.cells->>(e.col->>'key'), 10)),
                                        'last', max(left(r.cells->>(e.col->>'key'), 10)))
                                 FROM doc_table_rows r
                                WHERE r.table_id = t.id AND r.cells ? (e.col->>'key')
                               HAVING count(*) > 0), e.col)
                        ELSE e.col END
                      ORDER BY e.ord)
                 FROM jsonb_array_elements(t.columns) WITH ORDINALITY AS e(col, ord))
         WHERE EXISTS (
               SELECT 1 FROM jsonb_array_elements(t.columns) c
                WHERE c->>'type' = 'date' AND NOT c ? 'first');
        -- date_column_span:end
        INSERT INTO schema_marks (name) VALUES ('date_column_span');
    END IF;
    -- Figures "read" from a sheet's own column description ("12500 rows") by
    -- Chart mode's live prose read, before it skipped those pages.
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'no_prose_tables_from_sheets') THEN
        DELETE FROM doc_tables t
         WHERE t.origin = 'text'
           AND EXISTS (SELECT 1 FROM chunks c
                        WHERE c.document_id = t.document_id AND c.org_id = t.org_id
                          AND c.chunk_index = 0
                          AND starts_with(c.content, 'Spreadsheet table "'));
        INSERT INTO schema_marks (name) VALUES ('no_prose_tables_from_sheets');
    END IF;
END $$;

-- A chart a member kept: personal `(org_id, user_id)`, never shared. Stores
-- the SPEC, so it stays current (unlike `scheduler_reports`, a snapshot).
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

-- Pinning twice is a no-op. Partial indexes: NULL workspace_id = the company.
CREATE UNIQUE INDEX IF NOT EXISTS uq_insight_pins_org
    ON insight_pins (org_id, user_id, metric, period, coalesce(group_by, ''))
    WHERE workspace_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_insight_pins_space
    ON insight_pins (org_id, user_id, workspace_id, metric, period,
                     coalesce(group_by, ''))
    WHERE workspace_id IS NOT NULL;

---------------------------------------------------------------------------
-- In-chat file attachments: a file a person drops into one conversation.
-- Never embedded (it is not corpus, and `documents` is shared by the scope).
-- The bytes and text live in the object store (see below); `user_id` is
-- checked on every read. Cascades from the conversation.
CREATE TABLE IF NOT EXISTS conversation_attachments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    org_id          UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    user_id         UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    filename        TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    content         TEXT NOT NULL,
    char_count      INT  NOT NULL,
    -- True when the file was longer than the char budget: told to the asker and
    -- stated in the prompt.
    truncated       BOOLEAN NOT NULL DEFAULT false,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversation_attachments_owner
    ON conversation_attachments (conversation_id, org_id, user_id, created_at);

-- Injection score at upload (app/guard/live.py); NULL = unscored. A flagged
-- file is still accepted, but its prompt text leads with a warning.
ALTER TABLE conversation_attachments ADD COLUMN IF NOT EXISTS injection_score REAL;

-- Tables inside an UPLOADED file: `attachment_id` set, `document_id` NULL
-- (exactly one). Access is the attachment's (uploader, that conversation);
-- `list_tables` joins documents, so uploads never reach anyone else's chart.
-- Cascades with the attachment.
ALTER TABLE doc_tables ADD COLUMN IF NOT EXISTS attachment_id UUID
    REFERENCES conversation_attachments (id) ON DELETE CASCADE;
ALTER TABLE doc_tables ALTER COLUMN document_id DROP NOT NULL;
ALTER TABLE doc_tables DROP CONSTRAINT IF EXISTS doc_tables_one_source;
ALTER TABLE doc_tables ADD CONSTRAINT doc_tables_one_source
    CHECK ((document_id IS NULL) <> (attachment_id IS NULL));
CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_tables_attachment_origin
    ON doc_tables (attachment_id, origin, position) WHERE attachment_id IS NOT NULL;
-- When figures were last read from this upload's prose (Chart mode). NULL =
-- never; set even when none were found.
ALTER TABLE conversation_attachments ADD COLUMN IF NOT EXISTS figures_read_at TIMESTAMPTZ;

---------------------------------------------------------------------------
-- Feedback and documentation gaps. `refusal = true, rating NULL` is written
-- automatically for an ungrounded answer; `rating = +1/-1` is a thumb with an
-- optional reason and comment. Question and answer are snapshotted (turns may
-- be swept, and some paths write none). `normalized_question` comes from the
-- resolved question. `user_id` is SET NULL (a gap outlives the person) and
-- kept so counts are distinct people. `gate_score` NULL = nothing matched;
-- below RAG_SIMILARITY_THRESHOLD = close but not close enough.
CREATE TABLE IF NOT EXISTS feedback_and_gaps (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id        UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    user_id             UUID REFERENCES users (id) ON DELETE SET NULL,
    conversation_id     UUID REFERENCES conversations (id) ON DELETE CASCADE,
    question            TEXT NOT NULL,
    normalized_question TEXT NOT NULL,
    answer              TEXT,
    -- Which agent answered and where ('web' | 'slack'): a gap on one surface only
    -- is a routing bug.
    agent               TEXT,
    surface             TEXT NOT NULL DEFAULT 'web',
    refusal             BOOLEAN NOT NULL DEFAULT false,
    gate_score          REAL,
    rating              SMALLINT CHECK (rating IN (-1, 1)),
    reason              TEXT,
    comment             TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_feedback_org_recent
    ON feedback_and_gaps (org_id, created_at DESC);
-- The gap view groups by normalized question within an org and a window.
CREATE INDEX IF NOT EXISTS idx_feedback_org_question
    ON feedback_and_gaps (org_id, normalized_question);

-- Gaps grouped by MEANING (`app/feedback/topics.py`). A topic is its first
-- question and embedding; later questions join the nearest in the same scope.
-- After `handled_at`, only newer questions count.
CREATE TABLE IF NOT EXISTS gap_topics (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    embedding    halfvec(1024) NOT NULL,
    handled_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gap_topics_scope ON gap_topics (org_id, workspace_id);

-- Convert `vector(1024)` embeddings to halfvec in place (a cast, no
-- re-embedding) and rebuild the HNSW index; the ALTER locks `chunks`.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'embeddings_halfvec') THEN
        IF format_type((SELECT atttypid FROM pg_attribute
                         WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'),
                       NULL) = 'vector' THEN
            DROP INDEX IF EXISTS idx_chunks_embedding;
            ALTER TABLE chunks
                ALTER COLUMN embedding TYPE halfvec(1024) USING embedding::halfvec(1024);
        END IF;
        IF format_type((SELECT atttypid FROM pg_attribute
                         WHERE attrelid = 'gap_topics'::regclass AND attname = 'embedding'),
                       NULL) = 'vector' THEN
            ALTER TABLE gap_topics
                ALTER COLUMN embedding TYPE halfvec(1024) USING embedding::halfvec(1024);
        END IF;
        INSERT INTO schema_marks (name) VALUES ('embeddings_halfvec');
    END IF;
END $$;

-- HNSW cosine index, per partition. Isolation does not depend on it: the
-- org_id filter and row-level policy do.
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON chunks USING hnsw (embedding halfvec_cosine_ops);

ALTER TABLE feedback_and_gaps
    ADD COLUMN IF NOT EXISTS topic_id UUID REFERENCES gap_topics (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_feedback_topic ON feedback_and_gaps (topic_id);

-- Attachment bytes and text moved to the object store (original plus a
-- plaintext companion); the row keeps what finds them. `content` stays,
-- NULLABLE, so older rows keep answering (`load_attachment_texts` falls back).
ALTER TABLE conversation_attachments ADD COLUMN IF NOT EXISTS storage_key TEXT;
ALTER TABLE conversation_attachments ALTER COLUMN content DROP NOT NULL;

-- One row per person as each CONNECTOR knows them (Slack member, Drive editor,
-- GitHub login). `user_id` is set ONLY on proof: provider_email (same email in
-- the same org) or oauth (they signed in). Never by display name. A link
-- changes graph attribution, never access. `external_id` is the stable id or
-- `email:<addr>`; `user_id` SET NULL on member delete.
CREATE TABLE IF NOT EXISTS person_identities (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    email        TEXT,
    display_name TEXT,
    user_id      UUID REFERENCES users (id) ON DELETE SET NULL,
    verified_by  TEXT,
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, provider, external_id)
);
CREATE INDEX IF NOT EXISTS idx_person_identities_user
    ON person_identities (org_id, user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_person_identities_email
    ON person_identities (org_id, email) WHERE email IS NOT NULL;

-- The knowledge graph: one per scope, filtered per viewer at read. IDs and
-- relationships only, so nothing outlives a revocation; document evidence is
-- checked with the ONE visibility predicate on every walk.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- `key` is stable across re-syncs (`<provider>:<external id>`, not
-- `documents.id`, which changes). People are per connector identity; a linked
-- member gets a `user:<id>` entity joined by `same_person` edges.
CREATE TABLE IF NOT EXISTS kg_entities (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    kind         TEXT NOT NULL,
    key          TEXT NOT NULL,
    name         TEXT NOT NULL,
    aliases      TEXT[],
    document_id  UUID REFERENCES documents (id) ON DELETE SET NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_entities_org
    ON kg_entities (org_id, key) WHERE workspace_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_entities_space
    ON kg_entities (org_id, workspace_id, key) WHERE workspace_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_kg_entities_name_trgm
    ON kg_entities USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_kg_entities_aliases ON kg_entities USING gin (aliases);
CREATE INDEX IF NOT EXISTS idx_kg_entities_document ON kg_entities (document_id);

-- `valid_to` NULL = current; changes are CLOSED, not deleted. One current edge
-- per (src, dst, relation).
CREATE TABLE IF NOT EXISTS kg_edges (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    src_id       UUID NOT NULL REFERENCES kg_entities (id) ON DELETE CASCADE,
    dst_id       UUID NOT NULL REFERENCES kg_entities (id) ON DELETE CASCADE,
    relation     TEXT NOT NULL,
    valid_from   TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to     TIMESTAMPTZ,
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_kg_edges_current
    ON kg_edges (src_id, dst_id, relation) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_kg_edges_src ON kg_edges (org_id, src_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_dst ON kg_edges (org_id, dst_id);

-- WHY an edge exists, and so who may see it; exactly one kind per row:
-- document_id (visible iff the viewer may open it), fact_id (scope-level),
-- or neither (`is_public`/`viewers`). NULL `is_public` there is NOT public.
CREATE TABLE IF NOT EXISTS kg_evidence (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    edge_id     UUID NOT NULL REFERENCES kg_edges (id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents (id) ON DELETE CASCADE,
    fact_id     UUID REFERENCES activity_facts (id) ON DELETE CASCADE,
    is_public   BOOLEAN,
    viewers     TEXT[]
);
CREATE INDEX IF NOT EXISTS idx_kg_evidence_edge ON kg_evidence (edge_id);
CREATE INDEX IF NOT EXISTS idx_kg_evidence_document ON kg_evidence (document_id);
CREATE INDEX IF NOT EXISTS idx_kg_evidence_fact ON kg_evidence (fact_id);

-- Live-read audit: one row per live read. Never the token, never the result text.
CREATE TABLE IF NOT EXISTS live_tool_calls (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id    UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    user_id         UUID REFERENCES users (id) ON DELETE SET NULL,
    conversation_id UUID,
    provider        TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    mode            TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    truncated       BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms      INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_live_tool_calls_org_time
    ON live_tool_calls (org_id, created_at DESC);

-- Personal memory: a few facts per person, written only from their own
-- questions, used to read the question, never as evidence. Private to
-- (org_id, user_id). Dies with its chat unless pinned.
ALTER TABLE organizations ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT TRUE;
CREATE TABLE IF NOT EXISTS user_memory (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                 UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    user_id                UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    kind                   TEXT NOT NULL,
    text                   TEXT NOT NULL,
    source_conversation_id UUID REFERENCES conversations (id) ON DELETE CASCADE,
    pinned                 BOOLEAN NOT NULL DEFAULT FALSE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE user_memory ADD COLUMN IF NOT EXISTS announced BOOLEAN NOT NULL DEFAULT FALSE;
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_memory_text
    ON user_memory (org_id, user_id, lower(text));
CREATE INDEX IF NOT EXISTS idx_user_memory_owner ON user_memory (org_id, user_id, created_at DESC);

-- Agent mode (Act): tables no read path touches.
--
-- A person's OWN token for acting in a tool; never used for reads or sync.
-- Cascades with the person.
CREATE TABLE IF NOT EXISTS user_connections (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    user_id                 UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    provider                TEXT NOT NULL,
    external_user_id        TEXT NOT NULL,
    external_user_name      TEXT,
    scopes                  TEXT[] NOT NULL DEFAULT '{}',
    access_token_encrypted  TEXT NOT NULL,
    refresh_token_encrypted TEXT,
    expires_at              TIMESTAMPTZ,
    needs_reauth            BOOLEAN NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, provider)
);

-- Which actions are switched on. workspace_id NULL is the org's setting; a
-- space's row can only switch an action OFF there (policy.is_enabled).
CREATE TABLE IF NOT EXISTS action_policies (
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    action       TEXT NOT NULL,
    enabled      BOOLEAN NOT NULL,
    updated_by   UUID REFERENCES users (id) ON DELETE SET NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_policies_orgwide
    ON action_policies (org_id, action) WHERE workspace_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_policies_workspace
    ON action_policies (org_id, workspace_id, action) WHERE workspace_id IS NOT NULL;

-- One proposed write, proposal to outcome: the approval queue and the audit
-- log in one. The stored payload is what runs. A public exposure is refused by
-- a CHECK.
CREATE TABLE IF NOT EXISTS actions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id    UUID REFERENCES workspaces (id) ON DELETE SET NULL,
    user_id         UUID REFERENCES users (id) ON DELETE SET NULL,
    conversation_id UUID,
    surface         TEXT NOT NULL CHECK (surface IN ('web', 'slack')),
    action          TEXT NOT NULL,
    target          JSONB NOT NULL,
    payload         JSONB NOT NULL,
    payload_hash    TEXT NOT NULL,
    sources         JSONB NOT NULL DEFAULT '[]',
    exposure        TEXT NOT NULL DEFAULT 'none'
                    CHECK (exposure IN ('none', 'narrower', 'wider', 'unknown')),
    acknowledged    BOOLEAN NOT NULL DEFAULT false,
    status          TEXT NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed', 'executing', 'succeeded', 'failed',
                                      'unknown', 'rejected', 'expired')),
    expires_at      TIMESTAMPTZ NOT NULL,
    decided_at      TIMESTAMPTZ,
    executed_at     TIMESTAMPTZ,
    result          JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_actions_org_time ON actions (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_actions_user_status ON actions (org_id, user_id, status);
CREATE INDEX IF NOT EXISTS idx_actions_proposed_expiry
    ON actions (expires_at) WHERE status = 'proposed';
-- Act from Slack: where a proposal was made decides what it may read
-- and where it may post, and an admin switches each Slack surface on apart.
ALTER TABLE actions DROP CONSTRAINT IF EXISTS actions_surface_check;
ALTER TABLE actions ADD CONSTRAINT actions_surface_check
    CHECK (surface IN ('web', 'slack_dm', 'slack_channel'));
-- Editable drafts: the model's draft as first proposed, kept on the first
-- edit so the log shows both what the model wrote and what was approved.
ALTER TABLE actions ADD COLUMN IF NOT EXISTS original_payload JSONB;

-- Chains: a request whose next step needs an approved card's result. The
-- model's conversation waits for the cards; pinned to org and user, and
-- lives no longer than its cards.
CREATE TABLE IF NOT EXISTS act_runs (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id             UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id       UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    user_id            UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    surface            JSONB NOT NULL,
    question           TEXT NOT NULL,
    model              TEXT,
    messages           JSONB NOT NULL,
    found              JSONB NOT NULL DEFAULT '[]',
    sources            JSONB NOT NULL DEFAULT '[]',
    -- waiting: paused at its cards' calls, answered once they are decided;
    -- standby: an older paused run, continued the same way.
    status             TEXT NOT NULL DEFAULT 'waiting'
                       CHECK (status IN ('waiting', 'standby', 'resuming', 'done')),
    steps              INT NOT NULL DEFAULT 1,
    drafted            JSONB NOT NULL DEFAULT '[]',
    expires_at         TIMESTAMPTZ NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_act_runs_owner ON act_runs (org_id, user_id, status);
ALTER TABLE act_runs DROP CONSTRAINT IF EXISTS act_runs_status_check;
ALTER TABLE act_runs ADD CONSTRAINT act_runs_status_check
    CHECK (status IN ('waiting', 'standby', 'resuming', 'done'));
ALTER TABLE actions ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES act_runs (id) ON DELETE SET NULL;

-- MCP servers, added by admins only. Tools start OFF; ``tool_modes`` holds
-- each tool's mode ("lookup" or "write") with its schema hash, so a changed
-- tool is off again. Each person signs in (user_connections 'mcp:<id>').
CREATE TABLE IF NOT EXISTS mcp_servers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    url         TEXT NOT NULL,
    auth        TEXT NOT NULL CHECK (auth IN ('none', 'oauth')),
    oauth       JSONB,
    tools       JSONB NOT NULL DEFAULT '[]',
    tool_modes  JSONB NOT NULL DEFAULT '{}',
    enabled     BOOLEAN NOT NULL DEFAULT true,
    created_by  UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_servers_org_url ON mcp_servers (org_id, url);
-- An MCP sign-in uses PKCE; its verifier (encrypted) waits on the state row.
ALTER TABLE oauth_states ADD COLUMN IF NOT EXISTS code_verifier TEXT;

-- Writes go only through MCP since 2026-10-22: remove the old built-in
-- actions' tokens and switches once. MCP sign-ins and Agent switches stay.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM schema_marks WHERE name = 'act_mcp_only') THEN
        DELETE FROM user_connections WHERE provider NOT LIKE 'mcp:%';
        DELETE FROM action_policies
         WHERE action <> 'act' AND action NOT LIKE 'surface:%';
        INSERT INTO schema_marks (name) VALUES ('act_mcp_only');
    END IF;
END $$;

-- One chunks partition per org in its own schema, readable only through
-- `chunks` and its row-level policy. SECURITY DEFINER: only the owner of
-- `chunks` may add or drop a partition.
CREATE SCHEMA IF NOT EXISTS chunk_partitions;

CREATE OR REPLACE FUNCTION create_chunk_partition(org UUID) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS chunk_partitions.%I PARTITION OF chunks FOR VALUES IN (%L)',
        'org_' || replace(org::text, '-', ''), org);
END $$;
REVOKE EXECUTE ON FUNCTION create_chunk_partition(UUID) FROM PUBLIC;

CREATE OR REPLACE FUNCTION chunk_partition_on_org_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    PERFORM create_chunk_partition(NEW.id);
    RETURN NEW;
END $$;

-- BEFORE the delete: dropping the partition removes its rows at once, instead
-- of the cascade deleting them one by one, and leaves no empty table behind.
CREATE OR REPLACE FUNCTION chunk_partition_on_org_delete() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    EXECUTE format('DROP TABLE IF EXISTS chunk_partitions.%I',
                   'org_' || replace(OLD.id::text, '-', ''));
    RETURN OLD;
END $$;

DROP TRIGGER IF EXISTS organizations_chunk_partition_insert ON organizations;
CREATE TRIGGER organizations_chunk_partition_insert
    AFTER INSERT ON organizations
    FOR EACH ROW EXECUTE FUNCTION chunk_partition_on_org_insert();
DROP TRIGGER IF EXISTS organizations_chunk_partition_delete ON organizations;
CREATE TRIGGER organizations_chunk_partition_delete
    BEFORE DELETE ON organizations
    FOR EACH ROW EXECUTE FUNCTION chunk_partition_on_org_delete();

-- Every organization has its partition, including one created by a process
-- still running code from before the trigger.
SELECT create_chunk_partition(id) FROM organizations;

-- The one-time copy out of the table moved aside at the top of this file.
DO $$
BEGIN
    IF to_regclass('chunks_unpartitioned') IS NOT NULL THEN
        INSERT INTO chunks (id, org_id, document_id, chunk_index, content, embedding,
                            created_at, injection_score, injection_model, workspace_id)
        SELECT id, org_id, document_id, chunk_index, content, embedding::halfvec(1024),
               created_at, injection_score, injection_model, workspace_id
          FROM chunks_unpartitioned;
        DROP TABLE chunks_unpartitioned;
        INSERT INTO schema_marks (name) VALUES ('chunks_partitioned_by_org')
            ON CONFLICT DO NOTHING;
    END IF;
END $$;

-- A company's own Google or Slack app (`app/auth/org_apps.py`). `app_key`
-- exists before the keys (it goes into the Slack template), so client_id NULL
-- means not set up; it only picks a Slack signing secret.
CREATE TABLE IF NOT EXISTS org_connector_apps (
    org_id                   UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    provider                 TEXT NOT NULL CHECK (provider IN ('google', 'slack')),
    app_key                  TEXT NOT NULL UNIQUE,
    client_id                TEXT,
    client_secret_encrypted  TEXT,
    signing_secret_encrypted TEXT,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, provider)
);

-- Row-level security on chunks. Pooled connections act as `handbook_app` and
-- see only the transaction's org (`get_connection(org_id=...)`), none if
-- unset. `app.all_orgs` is for cross-tenant background jobs. Migrations run
-- as the owner, which the policy skips.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'handbook_app') THEN
        CREATE ROLE handbook_app NOLOGIN NOBYPASSRLS;
    END IF;
END $$;
GRANT handbook_app TO CURRENT_USER;
GRANT USAGE ON SCHEMA public TO handbook_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO handbook_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO handbook_app;

ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS chunks_one_org ON chunks;
CREATE POLICY chunks_one_org ON chunks TO handbook_app
    USING (org_id = nullif(current_setting('app.org_id', true), '')::uuid
           OR current_setting('app.all_orgs', true) = 'on')
    WITH CHECK (org_id = nullif(current_setting('app.org_id', true), '')::uuid
                OR current_setting('app.all_orgs', true) = 'on');
