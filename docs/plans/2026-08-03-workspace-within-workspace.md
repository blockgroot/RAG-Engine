# Workspace-within-a-Workspace Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let an authenticated employee of a tenant org (e.g. Syvora) create a personal **sub-workspace** inside their org, invite a handful of org colleagues into it, connect a personal Notion/Drive/Docs folder (e.g. meeting notes) to it, and have invited members ask questions grounded *only* in that sub-workspace's content — without weakening the existing org-level tenant isolation, gate, or grounding guarantees.

**Architecture:** Add one new isolation axis, `workspace_id`, that nests *inside* the existing `org_id` axis everywhere `org_id` already appears (chunks, documents, conversations, oauth_connections, ingestion_jobs). `workspace_id IS NULL` means "the org-wide space" (100% unchanged current behavior); a non-null `workspace_id` means "this row belongs to sub-workspace X, which itself belongs to org Y." Every retrieval/query gains a mandatory `(org_id, workspace_id)` pair instead of `org_id` alone — never `workspace_id` alone, for the same "ambiguity must be structurally impossible" reason `oauth_connections` is `UNIQUE(org_id, provider)` today. The RAG gate/prompt/reranker/memory pipeline is reused completely unchanged; only the *scope of what SQL rows it's allowed to see* changes.

**Tech Stack:** Same as the rest of the app — Postgres/pgvector, psycopg pool, FastAPI, Next.js App Router, existing `app/sources` / `app/auth` / `app/ingestion` / `app/rag` / `app/agent` packages. No new infra.

---

## 0. Why this shape (read before objecting to any task below)

1. **`workspace_id` nests inside `org_id`, it doesn't replace it.** A sub-workspace is *never* allowed to exist without a parent org, and a query for workspace content must always also carry the org_id — this keeps the existing `WHERE org_id = ...` isolation proof (`tests/test_isolation.py`) as a strict subset of the new proof, instead of introducing a second, parallel isolation mechanism that could be forgotten in one code path.
2. **Nullable `workspace_id`, not a separate `workspaces_chunks` table.** Every table that already carries `org_id` (chunks, documents, conversations, conversation_turns, oauth_connections, ingestion_jobs) gets one new nullable column. This matches the existing convention (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, additive, idempotent) instead of forking the schema into "personal" vs "org" variants that the whole ingestion/RAG pipeline would need two code paths for.
3. **A sub-workspace query sees ONLY its own workspace's rows, not the parent org's policies too** (`WHERE org_id = :org AND workspace_id = :workspace`, no `OR workspace_id IS NULL`). Rationale: a meeting-notes workspace answering "what's our leave policy?" by silently blending in HR policy chunks would surprise the user about *which* content grounded the answer, and it would make workspace membership meaningless as an access boundary (anyone in the workspace would transitively get full org content, when the workspace grant was only ever "meeting notes"). If mixed answering is wanted later, it should be an explicit opt-in toggle per workspace, not the default — flag this as a product decision in Task 12, don't silently build it in.
4. **Personal OAuth connections are workspace-scoped, not user-scoped globally.** `oauth_connections` today is `UNIQUE(org_id, provider)` — one Notion connection per org. A member's personal folder connection must NOT collide with (or overwrite) the admin's org-wide Notion connection, or a second member's personal connection. So the unique key becomes `UNIQUE(org_id, provider, workspace_id)` with `workspace_id` nullable — org-wide connections keep `workspace_id IS NULL` (zero behavior change for every existing admin flow), personal ones get their sub-workspace's id.
5. **Membership, not tenant boundary, gates who's inside a sub-workspace.** Unlike orgs (Notion-integration-enforced boundary, per CLAUDE.md §2), a sub-workspace's membership is purely our own `workspace_members` table — an invited colleague must already be a `users` row in the *same* `org_id`; you cannot invite someone outside the org into your sub-workspace. This keeps the org boundary as the outermost, Notion-enforced wall, and the sub-workspace as an inner, app-enforced wall.
6. **No new orchestrator, no new gate logic.** `RagPipeline`/`PolicyAgent`/the confidence gate/the strict prompt are reused byte-for-byte; they just get one more scoping parameter threaded through, mirroring exactly how `org_id` already flows through today. This preserves CLAUDE.md's core claim that "the gate is untouched" — a sub-workspace answer is refused/grounded by the identical logic an org-wide answer is.

---

## Phase A — Schema + core scoping primitive

### Task 1: Add `workspaces` and `workspace_members` tables

**Files:**
- Modify: `app/db/schema.sql`

**What to add** (append near the `users`/`oauth_connections` block, following existing conventions — UUID PK, `org_id` FK + index, idempotent):

```sql
-- Employee-created sub-workspaces (Workspace-within-a-Workspace). A
-- sub-workspace nests INSIDE its parent org — every row it owns still
-- carries org_id, so the org_id isolation proof stays a strict subset of
-- this new boundary rather than a second, parallel mechanism. `created_by`
-- is the employee who created it (always an org member, enforced at the API
-- layer, never a cross-org id).
CREATE TABLE IF NOT EXISTS workspaces (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_by UUID NOT NULL REFERENCES users (id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_workspaces_org ON workspaces (org_id);

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
    invited_by   UUID REFERENCES users (id),
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_workspace_members_user ON workspace_members (user_id);
```

**Step: Run migration**

Run: `python -m app.db.migrate` (or however `apply_schema` is invoked in this repo — check `scripts/init_db.py`)
Expected: no errors; `\d workspaces` / `\d workspace_members` show the new tables in psql.

**Step: Commit**

```bash
git add app/db/schema.sql
git commit -m "feat: add workspaces and workspace_members tables"
```

---

### Task 2: Thread `workspace_id` through every content-scoped table

**Files:**
- Modify: `app/db/schema.sql`

Add nullable `workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE` to every table that currently carries `org_id` for tenant scoping, **in this exact order** (documents/chunks first, since retrieval is the highest-stakes path):

```sql
-- Workspace-within-a-Workspace: NULL = org-wide (unchanged default; every
-- existing row and every existing query path is unaffected), non-NULL =
-- scoped to that sub-workspace. Never query workspace_id alone — always
-- paired with org_id (see app/rag/scope.py, Task 6).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_documents_workspace ON documents (workspace_id);

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_chunks_workspace ON chunks (workspace_id);

ALTER TABLE conversations ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_conversations_workspace ON conversations (workspace_id);

ALTER TABLE conversation_turns ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;

ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;
```

For `oauth_connections`, the unique constraint itself must change (Task 3 handles this separately since it's a constraint swap, not just an added column).

**Step: Run migration, verify idempotency**

Run: `python -m app.db.migrate` twice in a row.
Expected: second run is a no-op (all `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`), matching the existing convention proven by the current `schema.sql`.

**Step: Commit**

```bash
git add app/db/schema.sql
git commit -m "feat: add nullable workspace_id to documents/chunks/conversations/conversation_turns/ingestion_jobs"
```

---

### Task 3: Re-key `oauth_connections` for personal, workspace-scoped connections

**Files:**
- Modify: `app/db/schema.sql`

```sql
-- Workspace-within-a-Workspace: a personal connection (e.g. an employee's own
-- Notion "Meeting Notes" page) is scoped to their sub-workspace, not the org.
-- workspace_id NULL = today's org-wide admin connection (behavior unchanged).
-- The UNIQUE constraint must include workspace_id or a second member's
-- personal connection for the same provider would collide with (and silently
-- overwrite, via the existing ON CONFLICT upsert) an unrelated workspace's
-- connection or the org-wide one.
ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE;

ALTER TABLE oauth_connections DROP CONSTRAINT IF EXISTS oauth_connections_org_id_provider_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_connections_org_provider_workspace
    ON oauth_connections (org_id, provider, COALESCE(workspace_id, '00000000-0000-0000-0000-000000000000'));
```

(Postgres UNIQUE constraints treat NULL as distinct-from-NULL, which would let two NULL-workspace rows sneak through anyway on some engines' assumptions — but since `oauth_connections` already relies on a real UNIQUE constraint today for the org-wide case, use the `COALESCE`-to-sentinel unique index form so `workspace_id IS NULL` still behaves as "at most one org-wide row per provider", matching current behavior exactly, while distinct real `workspace_id`s each get their own row.)

**Step: Confirm the exact current constraint name**

Run: `psql "$DATABASE_URL" -c "\d oauth_connections"` and check the actual constraint name before writing the `DROP CONSTRAINT` line (Postgres auto-names it `oauth_connections_org_id_provider_key` for a 2-column UNIQUE, but verify against the live DB rather than assuming).

**Step: Run migration + sanity check**

Run: `python -m app.db.migrate`
Then: insert two rows with the same `(org_id, provider)` and different `workspace_id`s manually in a scratch DB; confirm both insert; then try a duplicate `workspace_id IS NULL` row for the same `(org_id, provider)` and confirm it's rejected — this proves the org-wide uniqueness guarantee CLAUDE.md documents today is preserved.

**Step: Commit**

```bash
git add app/db/schema.sql
git commit -m "feat: scope oauth_connections uniqueness to (org_id, provider, workspace_id)"
```

---

## Phase B — Application-layer scoping primitive (the thing every other package plugs into)

### Task 4: `app/workspaces/` package — membership + CRUD, no RAG logic

**Files:**
- Create: `app/workspaces/__init__.py`
- Create: `app/workspaces/store.py`
- Test: `tests/test_workspaces.py`

This is a small, single-implementation package like `app/jobs/` — **no `base.py`/factory**, since there's exactly one storage backend (Postgres), matching the existing convention that only genuinely-swappable capabilities get an interface.

```python
# app/workspaces/store.py
"""Sub-workspace CRUD + membership (Workspace-within-a-Workspace).

A sub-workspace always belongs to exactly one org (`org_id`); membership is
this module's OWN boundary — separate from, and stricter than, org
membership (every workspace member must already be a `users` row in the
same org, but not every org member is in every workspace). Never trust a
caller-supplied workspace_id without calling `assert_member` first — this
mirrors how app/api/deps.py is the only place org_id enters a request; this
module is the only place a workspace_id is validated against a user.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.exceptions import AuthError, NotFoundError  # add NotFoundError if not present
from ..db.connection import get_connection


@dataclass(frozen=True)
class WorkspaceInfo:
    id: str
    org_id: str
    name: str
    created_by: str
    role: str | None = None  # populated when listing "my workspaces"


def create_workspace(org_id: str, name: str, created_by_user_id: str) -> str:
    with get_connection() as conn:
        row = conn.execute(
            "INSERT INTO workspaces (org_id, name, created_by) VALUES (%s, %s, %s) RETURNING id",
            (org_id, name, created_by_user_id),
        ).fetchone()
        workspace_id = str(row[0])
        conn.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role, invited_by) "
            "VALUES (%s, %s, 'owner', %s)",
            (workspace_id, created_by_user_id, created_by_user_id),
        )
    return workspace_id


def invite_member(workspace_id: str, org_id: str, inviter_user_id: str, invitee_email: str) -> None:
    """Add an existing org user (by email) to the workspace.

    Requires the invitee to already be a `users` row with this SAME org_id —
    this is what stops a sub-workspace from becoming a side-channel around
    the org's Notion-enforced tenant boundary. Raises NotFoundError if the
    email isn't a member of this org (never auto-creates a user here — that
    stays magic-link/admin-invite's job, see app/auth/users.py).
    """
    with get_connection() as conn:
        user_row = conn.execute(
            "SELECT id FROM users WHERE email = %s AND org_id = %s", (invitee_email, org_id)
        ).fetchone()
        if not user_row:
            raise NotFoundError(f"{invitee_email!r} is not a member of this organization")
        conn.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role, invited_by) "
            "VALUES (%s, %s, 'member', %s) ON CONFLICT (workspace_id, user_id) DO NOTHING",
            (workspace_id, str(user_row[0]), inviter_user_id),
        )


def assert_member(workspace_id: str, org_id: str, user_id: str) -> str:
    """Return the caller's role in this workspace, or raise AuthError.

    ALWAYS checks org_id too (not just workspace_id + user_id) — a
    workspace's own org_id must match the caller's session org_id, so a
    stale/forged workspace_id from a different org 404s/403s instead of
    ever resolving. Call this before any read/write scoped to a workspace.
    """
    with get_connection() as conn:
        row = conn.execute(
            "SELECT wm.role FROM workspace_members wm "
            "JOIN workspaces w ON w.id = wm.workspace_id "
            "WHERE wm.workspace_id = %s AND wm.user_id = %s AND w.org_id = %s",
            (workspace_id, user_id, org_id),
        ).fetchone()
    if not row:
        raise AuthError("Not a member of this workspace")
    return row[0]


def list_my_workspaces(org_id: str, user_id: str) -> list[WorkspaceInfo]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT w.id::text, w.org_id::text, w.name, w.created_by::text, wm.role "
            "FROM workspaces w JOIN workspace_members wm ON wm.workspace_id = w.id "
            "WHERE w.org_id = %s AND wm.user_id = %s ORDER BY w.created_at DESC",
            (org_id, user_id),
        ).fetchall()
    return [WorkspaceInfo(id=r[0], org_id=r[1], name=r[2], created_by=r[3], role=r[4]) for r in rows]


def list_workspace_members(workspace_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT u.email, wm.role, wm.joined_at FROM workspace_members wm "
            "JOIN users u ON u.id = wm.user_id WHERE wm.workspace_id = %s ORDER BY wm.joined_at",
            (workspace_id,),
        ).fetchall()
    return [{"email": r[0], "role": r[1], "joined_at": r[2]} for r in rows]
```

**Step: Write `tests/test_workspaces.py`** covering:
- `create_workspace` creates it and auto-adds the creator as `owner`.
- `invite_member` succeeds for an existing same-org user, raises `NotFoundError` for an unknown email, and raises for a real user in a *different* org (proves the org-boundary check).
- `assert_member` raises `AuthError` for a non-member, and for a member of workspace A probing workspace B.
- `assert_member` raises when the workspace's org_id doesn't match the passed org_id (simulate a forged/stale id).
- `list_my_workspaces` only returns workspaces the user is a member of, scoped to the given org.

**Step: Run tests, commit**

```bash
pytest tests/test_workspaces.py -v
git add app/workspaces tests/test_workspaces.py
git commit -m "feat: add app/workspaces package for sub-workspace CRUD + membership"
```

---

### Task 5: Extend `VectorStore` reads/writes to accept an optional `workspace_id`

**Files:**
- Modify: `app/vectorstore/base.py`
- Modify: `app/vectorstore/pgvector_store.py`
- Test: `tests/test_isolation.py` (extend, don't replace)

Every method that currently takes `org_id` for scoping (`query`, `keyword_search`, `upsert_source_document`, `list_source_documents`, `delete_source_documents`, `acknowledge_source_document`) gains an additional keyword-only `workspace_id: str | None = None`:

- **Write paths** (`upsert_source_document`, `acknowledge_source_document`): store the passed `workspace_id` into the new `documents.workspace_id` / `chunks.workspace_id` columns (NULL when not passed — zero behavior change for existing org-wide ingestion).
- **Read paths** (`query`, `keyword_search`, `list_source_documents`): the `WHERE` clause becomes `WHERE org_id = %s AND workspace_id IS NOT DISTINCT FROM %s` (using `IS NOT DISTINCT FROM` instead of `=` specifically because `workspace_id = NULL` never matches in SQL — the org-wide case must match rows where `workspace_id IS NULL`, and `IS NOT DISTINCT FROM NULL` is exactly `IS NULL`). **Never accept `workspace_id` without also filtering `org_id`** — same rule as `assert_member`.
- **`delete_source_documents`**: same `IS NOT DISTINCT FROM` scoping so deleting workspace-scoped docs never touches org-wide ones and vice versa (this also fixes a subtlety: a sub-workspace's Notion connection and the org's admin Notion connection could otherwise both be `provider="notion"`, so `source_provider` alone is not enough to disambiguate their documents — `workspace_id` closes that gap).

**Step: Extend `tests/test_isolation.py`** with new cases (do NOT delete/weaken the existing org-vs-org cases):
- Chunks ingested with `workspace_id=W1` are invisible to a query with `workspace_id=None` (org-wide) for the same org — proves a sub-workspace's meeting notes never leak into the main policy chat.
- Chunks ingested with `workspace_id=W1` are invisible to a query with `workspace_id=W2` even when `org_id` matches — proves two sub-workspaces in the same org don't leak into each other.
- Org-wide chunks (`workspace_id=None`) remain visible to `workspace_id=None` queries exactly as before — regression guard that existing behavior is untouched.
- A query with the *wrong* `org_id` but the *right* `workspace_id` returns nothing — proves org_id is still load-bearing, not workspace_id alone.

**Step: Run full test_isolation.py + test_retrieval.py, commit**

```bash
pytest tests/test_isolation.py tests/test_retrieval.py -v
git add app/vectorstore tests/test_isolation.py
git commit -m "feat: thread optional workspace_id through VectorStore read/write paths"
```

---

### Task 6: `app/rag/scope.py` — a typed `RetrievalScope`, threaded through the pipeline

**Files:**
- Create: `app/rag/scope.py`
- Modify: `app/rag/pipeline.py`
- Modify: `app/rag/retrieval.py` (HybridRetriever)
- Modify: `app/agent/base.py`, `app/agent/policy_agent.py`
- Test: `tests/test_grounding.py` (add a workspace-scoped case), `tests/test_workspace_rag.py` (new)

Rather than threading a raw `(org_id, workspace_id)` tuple through every function signature (easy to accidentally swap/drop), introduce one small frozen dataclass:

```python
# app/rag/scope.py
"""The unit of tenant/workspace scoping passed through the RAG pipeline.

Every retrieval call takes a RetrievalScope, never a bare org_id string, so
it's structurally impossible to call VectorStore.query with an org_id but
forget workspace_id (or vice versa) — the two travel together from the API
layer (app/api/deps.py-resolved session + an explicitly validated
workspace_id) all the way to the SQL WHERE clause.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalScope:
    org_id: str
    workspace_id: str | None = None  # None = org-wide (unchanged existing behavior)

    @property
    def is_workspace(self) -> bool:
        return self.workspace_id is not None
```

`RagPipeline.answer(question, org_id, ...)` gains an optional `workspace_id: str | None = None` parameter, immediately wrapped into a `RetrievalScope` at the top of `_run()`/`answer()` and passed to every retrieval/reuse/memory call site instead of the bare `org_id`. **Do not change the gate/prompt logic at all** — they keep operating on whatever chunks retrieval hands them, exactly as today; only *which chunks retrieval is allowed to hand them* changes.

`conversation_last_retrieval`, `conversations`, `conversation_turns` reads/writes also carry `workspace_id` so a workspace conversation's reuse-check and summary never mix with the org-wide conversation history for the same user.

`PolicyAgent.answer(question, org_id, conversation_id=None, workspace_id=None)` passes it straight through — it remains a thin adapter (CLAUDE.md: "must not add behavior").

**Step: Write `tests/test_workspace_rag.py`** using the existing fake LLM/embedder fixtures (same style as `test_grounding.py`):
- Ingest a chunk with `workspace_id=W` only; ask a question with `workspace_id=W` → grounded answer citing it.
- Ask the SAME question against `workspace_id=None` (org-wide) in the same org → falls back (fixed fallback string), proving no leak.
- Ask against `workspace_id=W2` (a different workspace, same org) → falls back.
- A conversation started with `workspace_id=W`'s reuse/summary state is separate from an org-wide conversation for the same `conversation_id`-adjacent flows (or prove `conversation_id`s are workspace-scoped so this can't even arise — see Task 9).

**Step: Run tests, full suite, commit**

```bash
pytest tests/test_workspace_rag.py tests/test_grounding.py tests/test_conversation.py -v
pytest  # full suite, confirm nothing else regressed
git add app/rag app/agent tests/test_workspace_rag.py
git commit -m "feat: thread RetrievalScope (org_id + optional workspace_id) through RagPipeline/PolicyAgent"
```

---

## Phase C — Personal source connection + scoped ingestion

### Task 7: Extend OAuth connect flow to accept a target `workspace_id`

**Files:**
- Modify: `app/auth/credentials.py` (`save_connection`, `get_connection_token`, `get_live_connection_token`, `list_connections`, `set_connection_config`, `get_connection_config` — each gains optional `workspace_id: str | None = None`, threaded into the `WHERE`/`INSERT` exactly like Task 5's `IS NOT DISTINCT FROM` pattern)
- Modify: `app/api/auth.py` (the OAuth authorize/callback routes) — `oauth_states.workspace_id` column needed too (Task 2 addendum: add `workspace_id UUID REFERENCES workspaces(id)` to `oauth_states`), so the callback knows which workspace (if any) this connect flow was for.
- Modify: `app/db/schema.sql` — add `workspace_id` to `oauth_states`.

The **authorize** endpoint (`GET /auth/{provider}/authorize`) accepts an optional `?workspace_id=` query param. If present, `assert_member(workspace_id, org_id, user_id)` must succeed (any member can connect their OWN personal source into a workspace they belong to — decide in Task 12 whether only `owner` role may connect sources; recommend: yes, only `owner`, to avoid a member silently swapping the workspace's data source). The `state` row stores this `workspace_id` so the **callback** knows to call `save_connection(..., workspace_id=...)` instead of the org-wide path.

This is purely additive — every existing call site that doesn't pass `workspace_id` behaves exactly as it does today (admin connecting the org-wide Notion/Google source).

**Step: Add `test_auth.py` cases**
- Authorize with a `workspace_id` the caller is NOT a member of → 403, no `oauth_states` row created.
- Full authorize→callback roundtrip with a valid `workspace_id` → `oauth_connections` row has that `workspace_id`, distinct from the org-wide connection for the same provider.
- Existing org-wide authorize→callback (no `workspace_id`) → unchanged, still produces a `workspace_id IS NULL` row.

**Step: Run tests, commit**

```bash
pytest tests/test_auth.py tests/test_identity.py -v
git add app/auth app/api/auth.py app/db/schema.sql tests/test_auth.py
git commit -m "feat: scope OAuth connect flow to an optional workspace_id"
```

---

### Task 8: Scope the ingestion pipeline + job queue to a workspace

**Files:**
- Modify: `app/ingestion/pipeline.py` (`ingest_source`, `detect_source_changes` gain `workspace_id: str | None = None`, passed straight through to every `store.*` call from Task 5)
- Modify: `app/jobs/queue.py` (`enqueue` gains `workspace_id`; `ingestion_jobs.workspace_id` already added in Task 2)
- Modify: `app/jobs/worker.py` (reads `workspace_id` off the claimed job row, passes it into `ingest_source` and into `build_source_adapter`'s credential lookup — the adapter needs the workspace-scoped token from Task 7, via `get_live_connection_token(org_id, provider, workspace_id=...)`)
- Test: `tests/test_jobs.py` (extend)

No change to `SourceAdapter`/`NotionAdapter`/`GoogleDriveAdapter` themselves — they remain source-agnostic and don't know about workspaces at all; `workspace_id` only ever governs *where the fetched content is stored* and *which credential is used to fetch it*, both of which are the ingestion pipeline's job, not the adapter's.

**Step: Extend `tests/test_jobs.py`**
- Enqueue a job with `workspace_id=W`; worker processes it; resulting `documents`/`chunks` carry `workspace_id=W` (assert via `store.list_source_documents(org_id, provider, workspace_id=W)` returning them and `workspace_id=None` not returning them).
- A stuck-job reap still works identically regardless of `workspace_id` (reaper logic is status/timeout-based, doesn't need to know about workspaces at all — confirm no accidental coupling was introduced).

**Step: Run tests, commit**

```bash
pytest tests/test_jobs.py -v
git add app/ingestion app/jobs tests/test_jobs.py
git commit -m "feat: scope ingestion pipeline and job queue to an optional workspace_id"
```

---

## Phase D — HTTP API

### Task 9: `app/api/workspaces.py` router

**Files:**
- Create: `app/api/workspaces.py`
- Modify: `app/api/main.py` (register the router)
- Modify: `app/api/deps.py` — add a `get_workspace_scope` dependency
- Test: `tests/test_api_workspaces.py`

Endpoints, all requiring `get_session` (any authenticated org member, not `require_admin` — creating a personal workspace is an *employee* capability per the ask, distinct from org admin):

```
POST   /workspaces                       {name}                 -> {id}
GET    /workspaces                                               -> [{id, name, role, created_by}]
GET    /workspaces/{id}/members                                  -> [{email, role, joined_at}]
POST   /workspaces/{id}/members           {email}                -> 204   (owner-only)
GET    /workspaces/{id}/connections                               -> [...]   (reuses OAuthConnectionInfo shape)
GET    /auth/{provider}/authorize?workspace_id={id}               (Task 7 — existing route, new query param)
POST   /workspaces/{id}/ingest             {}                     -> {job_id}   (owner-only; enqueues, mirrors POST /admin/connections/{cid}/sync)
GET    /workspaces/{id}/jobs/{job_id}                             -> job status
```

Every route resolves `workspace_id` from the URL path, then IMMEDIATELY calls `assert_member(workspace_id, session.org_id, session.user_id)` (from Task 4) before touching anything else — this is `app/workspaces/store.py`'s `assert_member` doing for workspace-scoped routes exactly what `deps.get_session`/`require_admin` do for org-scoped routes: the one gate every downstream line trusts.

```python
# app/api/deps.py addendum
def get_workspace_role(
    workspace_id: str,
    session: SessionClaims = Depends(get_session),
) -> str:
    """Resolve + validate workspace membership; return the caller's role.

    Mirrors require_admin's shape but for the workspace boundary — the ONE
    place a workspace_id from a URL path is checked against the session's
    org_id + user_id before any router uses it.
    """
    from ..workspaces.store import assert_member
    from ..core.exceptions import AuthError
    try:
        return assert_member(workspace_id, session.org_id, session.user_id)
    except AuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def require_workspace_owner(role: str = Depends(get_workspace_role)) -> str:
    if role != "owner":
        raise HTTPException(status_code=403, detail="Workspace owner role required")
    return role
```

**Step: Write `tests/test_api_workspaces.py`**
- Create workspace → creator appears as owner in members list.
- Invite a same-org colleague → 204, appears in members list with role `member`.
- Invite an email not in the org → 404/400 (mirrors `invite_member`'s `NotFoundError`).
- A non-member hitting `GET /workspaces/{id}/members` → 403.
- A member (not owner) hitting `POST /workspaces/{id}/members` → 403.
- Cross-org: user from org B, given org A's real workspace id, gets 403 (not 200 with empty data, not 500) — this is the single most important test in this task, it's the HTTP-layer proof of Task 4's `assert_member` org check.

**Step: Run tests, commit**

```bash
pytest tests/test_api_workspaces.py -v
git add app/api/workspaces.py app/api/main.py app/api/deps.py tests/test_api_workspaces.py
git commit -m "feat: add /workspaces HTTP API (create, invite, connections, ingest)"
```

---

### Task 10: Scope `/chat/stream` to an optional workspace

**Files:**
- Modify: `app/api/chat.py`

`POST /chat/stream` body gains an optional `workspace_id`. If present:
1. `get_workspace_role`-equivalent check (member of it, org matches) — reuse `assert_member` directly, same as Task 9.
2. `conversation_id` (if supplied) must belong to BOTH `session.org_id` AND this `workspace_id` — extend `_conversation_belongs_to_org` into `_conversation_belongs_to_scope(conversation_id, org_id, workspace_id)` checking `conversations.workspace_id IS NOT DISTINCT FROM %s` too, so a workspace conversation id can't be replayed against the org-wide chat or a sibling workspace's chat.
3. Pass `workspace_id` through to `agent.answer_stream(...)`.

`POST /chat/conversations` (create_conversation) similarly accepts an optional `workspace_id`, validated the same way, and stores it on the new `conversations.workspace_id` column.

**Step: Extend `tests/test_api_chat.py`**
- Chat with `workspace_id` set, question answerable only from that workspace's ingested chunk → grounded answer.
- Same question, no `workspace_id` (org-wide) → fallback (no leak).
- A `conversation_id` created under workspace A, replayed in a request with workspace B (or no workspace_id) → 404, mirroring the existing cross-org conversation_id test.
- A user who isn't a member of the target workspace → 403, no agent call made (verify via a spy/mock that `agent.answer_stream` was never invoked, to prove the check happens before any DB/LLM work).

**Step: Run tests, commit**

```bash
pytest tests/test_api_chat.py -v
git add app/api/chat.py tests/test_api_chat.py
git commit -m "feat: scope chat streaming + conversations to an optional workspace_id"
```

---

## Phase E — Frontend

### Task 11: "My Workspaces" section

**Files:**
- Create: `frontend/app/workspaces/page.tsx` (list + create)
- Create: `frontend/app/workspaces/[id]/page.tsx` (members, connections, "ask" entry point)
- Modify: `frontend/app/chat/page.tsx` (or wherever the chat UI lives) — accept a `workspace_id` in the chat context/URL, pass it into the `/chat/stream` fetch body
- Reuse existing `ConnectionCard` component for the personal-connection UI (same provider-agnostic component the admin Sources page already uses) — pass it the workspace-scoped authorize URL from Task 7/9.

No new design system — follow the existing "Technical Editorial" plain-CSS-vars convention (CLAUDE.md §2, Phase 14). A workspace's chat view is the SAME chat component as the main org chat, parameterized by `workspace_id`; do not fork a second chat UI.

**Step: Manual verification** (per this repo's "start the dev server and use the feature in a browser" rule)
- `npm run dev` in `frontend/`, sign in as a real employee, create a workspace, invite a colleague (use a second test account in the same org), connect a personal Notion page, trigger ingest, ask a question in the workspace chat that's answerable only from that page, confirm grounded answer with citation; then ask the SAME question in the main org chat and confirm fallback (no leak) — this is the live proof mirroring how Phase 8/9 were verified against real Notion data.

**Step: Commit**

```bash
git add frontend/app/workspaces frontend/app/chat
git commit -m "feat: add My Workspaces UI (create, invite, connect, scoped chat)"
```

---

## Phase F — Decisions to make explicitly before/during Phase D (do not silently default)

### Task 12: Product decisions checklist

Resolve these with the user before or during API implementation — each has a "safe default" called out, but they change user-visible behavior:

1. **Can a sub-workspace see org-wide policy content in addition to its own?** Default: **no** (see §0.3). If the user wants "meeting notes + can still ask general HR questions," that's a second retrieval pass merged via RRF (mirrors Phase 6's hybrid-search fusion pattern) gated behind a per-workspace boolean (`workspaces.include_org_content BOOLEAN DEFAULT false`), NOT a blanket always-on merge.
2. **Who may create a workspace?** Default: **any authenticated org member** (per the ask — "as a successfully authenticated employee"), not just admins.
3. **Who may invite members / connect sources into an existing workspace?** Default: **owner only** (the creator); consider a `role='member'` cannot invite/reconnect, to stop an invited member from silently repointing the workspace's data source.
4. **Workspace membership cap?** The ask says "add two or three members" — recommend a soft cap (e.g. 10) enforced in `invite_member`, not hardcoded to 3, so it doesn't need a schema change later; flag as a config value (`WORKSPACE_MAX_MEMBERS` in `AppSettings`) rather than a magic number in code.
5. **Can a user belong to multiple sub-workspaces, and can an org have many?** Default: **yes to both**, unbounded — nothing in this design assumes one-per-user or one-per-org; `list_my_workspaces` already supports many.
6. **Does deleting/leaving a workspace delete its ingested content immediately?** Default: **yes**, via the existing `ON DELETE CASCADE` chain from `workspaces` → `documents`/`chunks`/`conversations`/`oauth_connections`/`ingestion_jobs` (Task 1/2/3 all used `ON DELETE CASCADE` for exactly this reason) — no soft-delete/grace-period unless requested.
7. **Rate limits / eval / observability parity:** should `rag.query_signals` (Phase 22) and `api_rate_counters` (Phase 21) include `workspace_id`? Recommend **yes**, add the column to both so workspace usage is observable the same way org usage already is — small addendum to Tasks 2 for those two tables, easy to fold in alongside the others.

---

## Test/verification summary (what "done" looks like)

- `tests/test_workspaces.py` — pure membership/CRUD unit tests (Task 4).
- `tests/test_isolation.py` — extended with workspace-vs-workspace and workspace-vs-org-wide leak proofs (Task 5), existing org-vs-org cases untouched.
- `tests/test_workspace_rag.py` — new, proves the gate/prompt/pipeline behave identically under workspace scoping, grounded + fallback cases (Task 6).
- `tests/test_auth.py` — extended with workspace-scoped OAuth connect (Task 7).
- `tests/test_jobs.py` — extended with workspace-scoped ingestion jobs (Task 8).
- `tests/test_api_workspaces.py` — new, HTTP-layer membership/ownership enforcement, esp. cross-org 403 (Task 9).
- `tests/test_api_chat.py` — extended with workspace-scoped chat + conversation_id cross-workspace replay rejection (Task 10).
- One live manual walkthrough against a real second Notion page + a second test employee account in the same org (Task 11), mirroring how every previous phase in this codebase was also verified live, not just via mocked tests.
- Full suite (`pytest`) green before merging, exactly as every prior phase in CLAUDE.md §6 reports.

## Rollout note for CLAUDE.md

Once implemented, add a new `## 2` bullet (own-shape reasoning: nested nullable `workspace_id` axis vs. a parallel schema), update the `## 3` folder map with `app/workspaces/`, add the new tables to `## 5`, and add a `## 6` "Built" entry — following this project's own stated rule to update the rulebook at the end of each phase.
