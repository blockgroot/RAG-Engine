# Second Brain Implementation Plan

**Goal:** Give Handbook a connected memory of *who and what relates to what*
across every connected tool, so normal Ask can answer questions that need
joining information from several places ("who should I talk to about the
token-refresh bug?"), and so a later deep research mode can gather evidence
with database queries instead of chains of LLM calls.

**Architecture:** Four layers, each with its own owner and access rule. Two
already exist (the index, conversation memory). Phase 1 adds an **identity
layer** and a **knowledge graph** stored in Postgres: three tables of IDs and
relationships, never copied text, where every edge carries *evidence* (the
document or fact it came from) and is visible to a viewer only if that evidence
is. The graph joins retrieval as one more ranked list under the unchanged 0.35
gate. It is built in the background from data sync already fetches. Nobody
sees the graph; people just get better answers.

**Tech stack:** Postgres + pgvector (Supabase), `pg_trgm`, psycopg, the
existing ingest worker, LangGraph (later phases). **No Neo4j. No Redis for
now:** the cache tier (Phase 1c) is deferred (2026-09-25) and nothing in the
current phases depends on it; the existing Postgres `query_answer_cache` stays
as it is.

**Prerequisite, shipped:** retrieval intent read from the question by the model
(breadth + time, `app/rag/query_intent.py`, PR #36). The graph builds on the
same retrieval path and the same fail-open posture.

---

## The invariants

1. **Nothing that has an access rule is copied.** The graph stores IDs and
   relationships; document text stays in `chunks`, sharing stays on
   `documents`. A copy outlives a revocation.
2. **One definition of "who may see what".** The graph, live tools and
   retrieval use the same `Viewer` (unrestricted / a person / public_only) and
   the **same SQL predicate**, `security/visibility.py::visibility_predicate`.
3. **Scope is storage, not a filter.** Every new row carries `org_id` +
   `workspace_id`. A walk never crosses a scope; a Slack DM *picks* one scope,
   it never blends two.
4. **Filter at every hop.** A walk must never pass through something the
   viewer cannot see, or the path itself discloses a relationship.
5. **Conversations never feed a shared layer.** Personal memory is private and
   learns only from the person's own questions.
6. **Retrieve many, generate few.** Gathering evidence costs zero LLM calls;
   only planning and writing do.
7. **Grounding is unchanged.** The 0.35 gate and the strict prompt stay as they
   are; new sources only add or reorder candidates.
8. **Bounded and honest.** Every walk and live call has a cap and marks when
   it was cut short (CLAUDE.md §2).

---

## The four layers

```
                ┌──────────────────────────────────────────────┐
  question ──►  │  ROUTER  (later: DEEP RESEARCH ORCHESTRATOR)  │
                └──┬───────────┬──────────────┬─────────────┬──┘
            ┌──────▼───┐ ┌─────▼──────┐ ┌─────▼─────┐ ┌─────▼──────┐
            │ A. INDEX │ │ B. GRAPH   │ │ C. PERSON │ │ LIVE TOOLS │
            │ what it  │ │ what's     │ │ who is    │ │ what's     │
            │ says     │ │ connected  │ │ asking    │ │ happening  │
            │ (exists) │ │ (Phase 1)  │ │ (later)   │ │ (later)    │
            └──────────┘ └────────────┘ └───────────┘ └────────────┘
   D. conversation memory (exists): running summary + last retrieval, per chat
```

| Layer | Owner | Built from | Status |
|---|---|---|---|
| A. Index | org/space, filtered per document | synced documents → chunks | exists |
| B. Knowledge graph | org/space, filtered per viewer | document metadata, `activity_facts`, native links | **Phase 1** |
| C. Personal memory | `(org_id, user_id)`, private | the person's own **questions** | later |
| D. Conversation memory | one chat | turns → summary | exists |
| Live tools | scope, filtered per viewer | connector APIs, read-only | later |

---

## Decisions

### Resolved

| # | Decision | Why |
|---|---|---|
| D1 | **One graph per scope (org / space), filtered per viewer at read time.** No per-user or per-conversation graphs. | N copies are N stale graphs and N places a revocation must reach. |
| D2 | **Postgres, not Neo4j.** Nodes and edges are rows; walks are recursive CTEs of ≤2–3 hops. | The access check stays one JOIN to `documents`, so revocation is automatic. Neo4j would need `doc_viewers` copied and kept in sync (the propagation machinery we declined to copy from Onyx), and it is a second service to host. AuraDB Free pauses and holds tenant data off-box; Community lacks fine-grained access control; Apache AGE is not on Supabase. |
| D3 | **Conversations never feed the shared graph.** | An answer can quote a document only the asker may see, and answers can be wrong. Conversation-derived facts go only to private personal memory (layer C). |
| D4 | **Identity: merge only on proof.** Auto-link when a connector's email equals a Handbook user's verified login email in the same org; otherwise the member links their own account through OAuth (GitHub first). **Never merge by display name. No admin-confirmation flow in v1**; an unproven identity simply stays unlinked. | Same pattern as Glean (directory + per-user GitHub OAuth). Two "Rahul"s must never become one person. Linking changes attribution only, never access. |
| D5 | **The graph is invisible.** No graph UI; the only new screen is "Linked accounts" in settings. | People care about the right answer, not the graph. |
| D6 | **Extraction in tiers, cheapest first, and LLM extraction only after measuring.** Tier 1 structured fields, tier 2 native mentions/links, tier 3 LLM extraction on changed chunks. | Regex alone is nearly useless: prod has only **23** chunks containing a Linear-style ID. Onyx built an LLM KG pipeline and deleted it as unused (onyx-dot-app/onyx#14780); value must be measured before paying for it. |
| D7 | **Postgres is the source of truth and, for now, the only store.** A Redis/Valkey hot tier is **deferred** (not in focus, 2026-09-25); if it is ever added it stays disposable. Conversations stay durable in Postgres; answers keep using the existing Postgres `query_answer_cache`. | Nothing in Phase 1 or 1d needs a cache, and a second store is one more service to host for every company deployment. Free Redis tiers are small or non-persistent; conversations back history, `?c=` links, the ownership check and the 30-day purge. |
| D8 | **Internal tool registry first, MCP later.** Handbook as an MCP *server* is a thin adapter over the registry, off by default per org. Handbook as a *client* of vendor MCP servers is not planned. | Vendor servers act with one shared token and apply no per-viewer filter, so a Drive search would return the connecting admin's whole Drive. |

### Open (answer before the phase that needs them)

| # | Question | Needed by |
|---|---|---|
| O1 | Is a separate background LLM endpoint (`LLM_AUX_BASE_URL` + key) available? Without it, tier-3 extraction stays parked. | 1d |
| O2 | ~~Redis host~~ — **parked with 1c** (deferred). Reopen only if 1c comes back. | 1c (deferred) |
| O3 | Include Google Workspace directory aliases in identity linking (needs a Workspace-admin connection, off by default)? | 1a (optional) |
| O4 | Does each tenant's Notion integration have the "read user information including email" capability? If not, Notion people stay unlinked nodes. | 1a |
| O5 | Deep research LLM-call budget per run (proposed ≤5). | deep research |
| O6 | Personal memory: saved automatically but visible/deletable (proposed), or confirmed by the user first? | personal memory |

---

## What reading the code established

- **People have no shared identity today.** The same person is "Sana Asiwal"
  in Drive (`lastModifyingUser(displayName)`) and Notion (`/users/{id}` →
  `name`), "sana" in Slack (`profile.display_name`), "Sana Asiwal" in Linear
  (`assignee { name }`) and "18-sana" in GitHub (`author.login`).
  `documents.source_last_editor` and `activity_facts.actor` store these display
  strings, so a graph built on them would either miss the match or merge by
  name. Hence step 1a.
- **Mentions are destroyed before storage.** Slack rewrites `<@U123>` into
  `@name` during fetch (`app/sources/slack.py:48`); Notion keeps only the plain
  text of a mention (`_rich_text_to_text`, `app/sources/notion.py:19`). Links
  must be captured **in the adapter, before that cleanup**, and stored with the
  document. They cannot be recovered from chunks.
- **Linear's structure is prose.** Team, state, assignee and labels exist only
  as the preamble inside chunk 1; the identifier is in the title
  (`ENG-142 - Fix login`). The GraphQL query can return them as fields for free.
- **`activity_facts` is already half a graph.** `pr_opened`, `pr_merged`,
  `pr_reviewed`, `commit`, `issue_completed`, `issue_state`, `doc_changed`, each
  with `actor` → `subject`.

---

## Security model

### Five layers

| Layer | Rule | Enforced by |
|---|---|---|
| Tenant | an org never sees another org | `org_id` on every graph row and in every WHERE |
| Scope | a space sees only its own rows; company Ask only org-wide rows | `workspace_id` on every row; per-scope nodes; walks never cross |
| Document | an edge is visible only if one of its evidence rows is | `kg_evidence` joined with the shared visibility predicate |
| Surface | a Slack channel reply is `public_only` | the same `Viewer`, passed into the walk |
| Personal | conversations and memory belong to their owner | the graph never reads conversation tables |

### Per connector

| Connector | Unit of access | Graph evidence visibility |
|---|---|---|
| Drive | per file (`doc_viewers`, groups expanded on read) | join to `documents` → the file's ACL. Owner-only fallback and the freeze apply automatically because they live on `documents`. Folder `part_of` edges inherit the **file's** evidence. |
| Slack | public channel = scope-public; private = members + `channel:<id>` | thread evidence → `documents`; `member_of` edges carry the **channel's ACL** (`is_public`/`viewers` on the evidence row), because private-channel membership is itself sensitive |
| GitHub | scope (authorized repos) | fact evidence → scope-level, as retrieval and charts today |
| Linear | scope (team membership not wired) | scope-level; inherits team ACLs automatically once they land on `documents` |
| Notion | scope (no per-page permission API) | scope-level; control is what is shared with the integration |

### Leaks explicitly blocked

- **Paths through hidden nodes:** the visibility check runs inside the
  recursive step, not after the walk.
- **Secret names:** a node is returned only through a visible edge, or when it
  is a document the viewer can open.
- **Counts:** degrees and neighbour counts are computed over visible edges.
- **Aliases:** v0 takes aliases only from structured fields; model-derived
  aliases (tier 3) carry evidence like edges.
- **Cache:** the only cache is the existing Postgres `query_answer_cache`;
  answers with non-public evidence are never stored there (the existing
  `_is_cacheable` rule). The ACL-hashed key scheme belongs to the deferred 1c.

---

## Schema

All additive, `IF NOT EXISTS`, placed after each table's `CREATE TABLE` in
`app/db/schema.sql` (CLAUDE.md §5), partial unique indexes for the
`workspace_id IS NULL` case.

```sql
-- 1a: identity + structured capture
ALTER TABLE documents      ADD COLUMN IF NOT EXISTS source_meta JSONB;       -- people, links, containers
ALTER TABLE documents      ADD COLUMN IF NOT EXISTS source_editor_key TEXT;  -- email or provider:<id>
ALTER TABLE activity_facts ADD COLUMN IF NOT EXISTS actor_key TEXT;          -- email or github:<login>

CREATE TABLE IF NOT EXISTS person_identities (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL,                  -- google | slack | notion | linear | github
    external_id  TEXT NOT NULL,                  -- provider user id / login
    email        TEXT,
    display_name TEXT,
    user_id      UUID REFERENCES users(id) ON DELETE SET NULL,
    verified_by  TEXT,                           -- provider_email | oauth | directory
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, provider, external_id)
);

-- 1b: the graph
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS kg_entities (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,  -- NULL = org-wide
    kind         TEXT NOT NULL,   -- person | document | folder | channel | team | repo | issue | pr | topic
    key          TEXT NOT NULL,   -- user:<id> | identity:<provider>:<id> | provider:<external id>
    name         TEXT NOT NULL,
    aliases      TEXT[],
    document_id  UUID REFERENCES documents(id) ON DELETE CASCADE
);
-- + partial unique indexes on (org_id, kind, key) WHERE workspace_id IS NULL
--   and (org_id, workspace_id, kind, key) WHERE workspace_id IS NOT NULL
-- + GIN (name gin_trgm_ops)

CREATE TABLE IF NOT EXISTS kg_edges (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id       UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces(id) ON DELETE CASCADE,
    src_id       UUID NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    dst_id       UUID NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    relation     TEXT NOT NULL,  -- edited | authored | assigned_to | opened | reviewed | merged
                                 -- | committed_to | member_of | part_of | references | mentions
    valid_from   TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to     TIMESTAMPTZ,    -- NULL = current; reassignment closes, never deletes
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- + (org_id, src_id) and (org_id, dst_id) indexes

CREATE TABLE IF NOT EXISTS kg_evidence (
    edge_id     UUID NOT NULL REFERENCES kg_edges(id) ON DELETE CASCADE,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    chunk_id    UUID REFERENCES chunks(id) ON DELETE CASCADE,
    fact_id     UUID REFERENCES activity_facts(id) ON DELETE CASCADE,
    is_public   BOOLEAN,         -- only for non-document evidence (channel membership)
    viewers     TEXT[]
);
```

Size estimate: 10k documents → ~5k entities, 100–200k edges; tens of MB.

---

## Phase 1: identity + structured knowledge graph in normal Ask

In scope: steps 1.0–1.7. Out of scope: the cache tier (1c, **deferred** —
not in focus), LLM extraction (1d), personal memory, live tools, deep research,
MCP.

### 1.0 One shared visibility predicate (refactor, no behaviour change)

**Status: done** (`app/security/visibility.py`, `tests/test_visibility.py`). Three copies
found, not one: the vector store, starter chips and the scheduler digest.

- **Move** `_VIEWER_SQL` / `_viewer_clause` out of
  `app/vectorstore/pgvector_store.py` into `app/security/visibility.py`.
- **Why:** the graph must decide "can this viewer see this document?" with the
  exact SQL retrieval uses. Two copies drift, and drift is a leak.
- **Done when:** `tests/test_doc_access.py` and `tests/test_isolation.py` pass
  unchanged.

### 1.1 Capture people, links and containers at sync (0 extra API calls)

`SourceDocument` gains `meta: dict | None`: `people` (provider, external id,
email, display name, role), `links` (target provider + external id or URL),
`containers` (folder / channel / team / parent page).

| Adapter | Added | Why it is free |
|---|---|---|
| Drive | `lastModifyingUser(displayName,emailAddress)`, `parents` | same `files.list` field list |
| Slack | author id + email, reply participants, `<@U…>`/`<#C…>` captured **before** `slack.py:48` | `users.info` already called per author; `users:read.email` already needed for private-channel ACLs |
| Notion | `last_edited_by` / `created_by` id + email (if capability), parent id, rich-text `mention` objects during the block walk | user lookup already cached per person |
| Linear | assignee/creator `{id name email}`, `team {id key name}`, `state {name type}`, labels, attached PR URLs | extra fields in the same GraphQL query |
| GitHub | `actor_key = github:<login>` on facts; Linear IDs from PR title/branch | already in the PR list payload |

- **Schema:** `documents.source_meta`, `documents.source_editor_key`,
  `activity_facts.actor_key`. The graph is rebuilt from the database, never
  by re-calling APIs. `source_last_editor` and `actor` stay for display and
  charts; nothing about provenance or charts changes.
- **Existing documents:** unchanged documents are never re-fetched, so add a
  **metadata-only refresh**: a bounded batch per tick that re-fetches and
  updates `source_meta` only — no re-chunking, no re-embedding.
- **Done when:** adapter fakes (Slack's rejects unexpected URLs) prove no new
  calls; mentions survive into `meta`.

**Status: done** (`app/sources/meta.py`, `tests/test_source_meta.py`).
Differences from the sketch above, each for a reason:

- Links are also read from the document BODY (`meta.extract_links`, in the
  pipeline, so every adapter gets it): a Linear or PR URL sits in a Drive
  doc's text as often as in a field. Only URL shapes that resolve to an id an
  adapter stores are kept.
- A MENTIONED Slack user and a Notion page's CREATOR are keyed by id with no
  lookup — resolving each would be the per-person call §5 warns about.
- A commit's `actor_key` is written only from a real login: `author` falls
  back to the git display name, which must never become an identity.
- `source_meta = {}` means "captured, nothing found"; NULL means "not captured
  yet", which is what the refresh (`refresh_missing_meta`,
  `GRAPH_META_REFRESH_BATCH=25` per job, `0` = off) looks for.
- **Found and fixed on the way:** deferred enrichment (on by default) rewrote
  every freshly synced row with only the run tags, so a restricted Drive file
  came back SCOPE-PUBLIC, a Slack thread lost its channel tag and its editor.
  It now replaces the CHUNKS only (`replace_source_document_chunks`) and leaves
  the row as ingest wrote it; re-reading sharing from its re-fetch would have
  skipped every Slack thread, whose sharing exists only on the listing.

### 1.2 Identity

- Table `person_identities`, upserted by the builder from `source_meta.people`.
- **Auto-link:** provider email == a Handbook user's email **in the same org**
  → `user_id`, `verified_by = provider_email`.
- **Self-serve link:** "Link your GitHub account" reuses the `github_oauth`
  authorize/exchange flow → `GET /user` → login → links `github:<login>` to the
  member (`verified_by = oauth`). Unlink route. Slack/Notion/Linear/Drive link
  through email, so GitHub is the only button in Phase 1.
- **UI:** "Linked accounts" in settings — the only frontend change this phase.
- **Rule:** a link changes attribution only, never access.
- **Done when:** two same-name identities never merge; an email matching a user
  in another org never links; nothing links without OAuth or a verified email;
  unlinking moves edges back to the unlinked identity.

**Status: done** (`app/graph/identities.py`, `app/api/account.py`,
`frontend/app/account/page.tsx`, `tests/test_identities.py`).

- The GitHub link REUSES `/auth/github/callback` (a GitHub App registers one
  callback URL, so a new route would need an App settings change). The
  callback routes on the STATE's provider (`github_link`) and finishes by
  consuming it under that provider, so neither flow can complete the other.
- The person comes from `oauth_states.user_id` on the consumed state, never
  the request: a forwarded callback URL links only whoever clicked.
- `GET /user` once, then the token is DISCARDED — linking needs proof, not access.
- Re-proving an already-linked GitHub account MOVES the link (logged).
- An email link is not unlinkable (the next sync would re-create it); it
  un-links itself when the connector's email stops matching.

### 1.3 Graph tables

`kg_entities`, `kg_edges` (with `valid_from`/`valid_to`), `kg_evidence`,
`pg_trgm` — per the schema above. Person key = `user:<id>` when linked, else
`identity:<provider>:<id>`.

**Status: done** (`app/db/schema.sql`, `tests/test_graph_builder.py`). Changes
from the sketch, each for a reason:

- Entities are unique on `(org_id[, workspace_id], key)`: the key already
  encodes provider and kind, so `kind` in the index adds nothing.
- A document entity's `document_id` is `ON DELETE SET NULL`, not CASCADE, and
  the KEY is `<provider>:<external id>`: an updated document is deleted and
  re-inserted with a new id, and the entity must survive that.
- **People are keyed per connector identity, always** (`identity:<provider>:<id>`);
  a linked member gets a `user:<id>` entity joined to each identity by a
  `same_person` edge. Linking or unlinking then rewrites only those edges
  (`rebuild_people`), instead of re-keying every edge the person has.
- `kg_evidence.chunk_id` is not created yet — nothing in Phase 1 produces
  chunk-level evidence (that is tier-3 extraction, Phase 1d).

### 1.4 The builder (`app/graph/builder.py`)

`app/graph/` is an orchestrator over one store, so no `base.py` (CLAUDE.md §2):
`store.py`, `builder.py`, `walk.py`, `linking.py`.

For each document an ingest job touched (`IngestResult.ingested_external_ids`):

1. delete that document's evidence rows;
2. upsert its nodes (document, people, containers);
3. insert edges + evidence from `source_meta`, tags and facts;
4. add `references` edges only to nodes that exist **in the same scope**;
5. garbage-collect edges left with no evidence.

| Relation | From → to | Source | LLM |
|---|---|---|---|
| `edited` / `authored` | person → document | `source_editor_key`, facts | no |
| `assigned_to` | issue → person | Linear assignee | no |
| `part_of` | issue → team, document → folder/channel/page, PR → repo | `source_meta`, tags | no |
| `opened` / `reviewed` / `merged` / `committed_to` | person → PR / repo | `activity_facts` | no |
| `member_of` | person → private channel | channel members (already fetched) | no |
| `references` | document → issue / PR / document | native mentions + URLs; regex IDs as fallback | no |

- **Hooks:** `worker.py` after `_record_insight_facts`, in its own try/except
  (a failure is a stale graph, never a failed job); `autosync.record_due_facts`
  for GitHub; the `connection_ops` disconnect purge; a backfill on the tick,
  like `backfill_all_document_facts`. Linking or unlinking an account triggers
  a SQL rebuild of that person's edges.
- **Done when:** running twice is idempotent; deleting a document removes its
  edges; a cross-scope reference is never created; disconnect purges.

**Status: done** (`app/graph/builder.py`). Notes:

- A reference target that ingests AFTER the page pointing at it is joined up
  when the target builds: the builder re-links documents in scope whose
  `source_meta.links` name it (≤200 per batch).
- A Linear link carries the IDENTIFIER (`ENG-142`), which resolves through the
  issue entity's `aliases`.
- Documents re-fetched by the metadata refresh are built too
  (`IngestResult.meta_refreshed_external_ids`).
- `member_of` (private-channel membership) is NOT built in Phase 1: membership
  is only stored as the threads' `doc_viewers`, and it is sensitive enough to
  wait for evidence rows that carry the channel's own ACL.

### 1.5 Linking and walking (`linking.py`, `walk.py`)

- **Linking:** exact identifiers in the question (`ENG-12`, `#14`, repo names)
  plus trigram matches on entity names; top 3, visible entities only.
- **Walk:** recursive CTE, **≤2 hops, ≤50 edges, current edges only**, the
  shared visibility predicate **inside the recursive step**; returns evidence
  document ids and a `truncated` flag.

```sql
WITH RECURSIVE walk(entity_id, depth) AS (
    SELECT id, 0 FROM kg_entities
     WHERE org_id = %(org)s AND <scope> AND id = ANY(%(seeds)s)
  UNION
    SELECT CASE WHEN e.src_id = w.entity_id THEN e.dst_id ELSE e.src_id END, w.depth + 1
      FROM walk w
      JOIN kg_edges e ON (e.src_id = w.entity_id OR e.dst_id = w.entity_id)
                     AND e.org_id = %(org)s AND <scope> AND e.valid_to IS NULL
     WHERE w.depth < 2
       AND EXISTS (SELECT 1 FROM kg_evidence ev
                   LEFT JOIN documents d ON d.id = ev.document_id
                   WHERE ev.edge_id = e.id AND <visible(ev, d, acl)>)
)
SELECT DISTINCT ... LIMIT %(cap)s;
```

- **Done when:** isolation holds both ways (org, space, document); a path
  through a hidden node reveals nothing beyond it; unsharing hides dependent
  edges on the next walk; a `public_only` viewer sees public evidence only; a
  walk costs a known, bounded number of round trips.

**Status: done** (`app/graph/linking.py`, `app/graph/walk.py`,
`tests/test_graph_walk.py`). Beyond the sketch:

- A DOCUMENT entity is entered only if the viewer may open that document, as
  well as the edge being visible. Edge visibility alone was not enough: a
  `references` edge is evidenced by the document that CONTAINS the link, so a
  readable page linking to an unreadable one would have revealed the second's
  title and led past it.
- Non-document evidence (GitHub facts, `same_person`) has its own rule in
  `security/visibility.py::evidence_predicate`, `is_public IS TRUE` so NULL
  fails closed. All access SQL still lives in that one file.
- `same_person` edges cost no hop, so a member's identities across tools
  count as one person.
- The outer query has no DISTINCT/ORDER: Postgres evaluates a recursive CTE
  only as far as the parent fetches, so the LIMIT stops the walk itself
  (breadth-first). `truncated` is set when the cap is what ended it.
- Three round trips per walk: seeds (`link_question`, one query), walk, evidence.

### 1.6 The graph as a retrieval list

- In `app/rag/retrieval.py::_first_stage_all`, add a ranked list next to
  vector, BM25 and recency: chunks from the walk's evidence documents, ranked
  by cosine to the question and **viewer-filtered again**.
- Fused by the existing RRF. **The gate does not change** — `gate_score` stays
  the best cosine.
- Caching is already safe: `_is_cacheable` refuses any non-public hit.
- `GraphSettings.from_env()` with `GRAPH_RETRIEVAL_ENABLED`, **off by
  default**. The builder always runs so the graph fills; the flag only decides
  whether answers use it.

**Status: done, OFF** (`app/rag/retrieval.py::_graph_documents`,
`VectorStore.query(document_ids=...)`, `tests/test_graph_retrieval.py`).

- One vector search, primary query only, restricted to the walk's evidence
  documents and filtered by the viewer AGAIN; fused by the existing RRF.
  Each hit carries a real cosine, so `gate_score` is unchanged.
- Linking + walk run before the first stage (three round trips) only when the
  flag is on; any failure drops the graph list and nothing else.
- Signals go to their own logger, `rag.graph_signals` (`graph_signal`: seeds,
  exact seeds, documents, edges, truncated; `graph_hits`: chunks it added),
  rather than being threaded through every `RagResult` path.

### 1.7 Measure before switching it on

- Add multi-hop questions to the golden set ("who reviewed the PR that fixed
  ENG-142?").
- Run the eval with the flag off and on; log `graph_hits` in query signals.
- Enable in prod only if answers improve and nothing else regresses.

**Status: measurement built; the switch is NOT thrown** (`evaluation/graph_eval.py`,
`tests/test_graph_eval.py`). The multi-hop questions live in their own seeded
corpus rather than the policy golden set, because they need people, links and
facts the policy corpus does not have; the policy corpus rides along as
distractors. `python -m evaluation.graph_eval` runs every case with the graph
list off and on and prints a verdict: **enable only on a gain with no loss**.
With a stand-in hashing embedder it reports +1 case, nothing lost — that proves
the machinery, not the value. Run it with the real embedder before switching
`GRAPH_RETRIEVAL_ENABLED` on in production.

---

## Phase 1c: cache tier — DEFERRED (not in focus)

**Decision (2026-09-25): skip the cache tier for now.** Nothing in Phase 1 or
1d depends on it, the existing Postgres `query_answer_cache` already covers
repeated answers, and adding Redis would mean one more service to host for
every company deployment. The design below is kept only so it does not have to
be re-derived if latency measurements later justify it; do not build it as
part of the current work. **Next after Phase 1 is 1.7's measurement, then 1d.**


- `app/cache/`: `base.py` + `redis` + `null` + `factory.py` (two real backends,
  so the package is justified). No `REDIS_URL` → cache **off**, never a fallback
  to the database.
- **Holds only recomputable data:** answers (moving `query_answer_cache` off
  Postgres), walk results, and **active conversation context** (summary + last
  N turns) written through on each turn with a ~24h TTL. Postgres stays the
  durable record for history, `?c=` links, ownership and the 30-day purge; a
  Redis restart reloads from it.
- **Key scheme:**
  `org:{org_id}:v{org_version}:ws:{workspace}:acl:{hash(viewer.acl())}:model:{m}:{question_hash}`.
  `org_version` increments on ingest, disconnect and revocation, making every
  old key for that org cold with no key scans.
- **Never cached:** anything with non-public evidence, access notices.
- Self-hosted: Valkey in `docker-compose`. Hosted: per O2.

## Phase 1d: LLM extraction (after 1.7 shows the structured graph is used)

- LightRAG-style: entities and relations from **new or changed chunks only**
  (the sync diff already knows which), on the **aux** endpoint, with a
  per-org extraction budget. Closed entity types: person, team, project,
  system/service, customer, decision.
- **Resolution (Graphiti-style, scope-bounded):** candidates by trigram +
  embedding within the same org/space and type → LLM confirms → otherwise a
  new node.
- Every extracted edge cites its **chunk** as evidence and inherits that
  document's ACL; anything that cannot be tied to a chunk is discarded.
- A person named in text links to a real person only when exactly one
  *verified* identity in that scope matches; otherwise it stays a mention node.
- No community detection or summaries (GraphRAG's cost is exactly what the
  15 rpm budget cannot carry).

---

## Later phases (outline)

**Personal memory.** `user_memory (org_id, user_id, kind, text,
source_conversation_id ON DELETE CASCADE, pinned, last_used_at)`, ~30 per
person, no embeddings in v1. Written by the existing background summary-fold
call from the person's **questions only** (never from answers, which would copy
document content past its ACL). Used to rewrite the question and set tone,
never as evidence; **off in Slack channel replies**. A "What Handbook remembers
about you" page with delete and pin (pin detaches from the chat so it survives
the 30-day purge).

**Live tool registry.** `app/tools/` (base + one implementation per connector
+ factory), read-only, each tool declaring an access mode: Drive
`acl_filtered` (limited to `folder_id`, permissions read in the same listing,
mapped with the ingest `DocAccess` rules), Slack `membership`, GitHub / Linear
/ Notion `scope`. Bounded, `truncated`-marked, fenced through `untrusted.py`,
labelled `source="live"`. A per-provider pacer reserves headroom for sync.
`githublive`'s six tools move under it. Check the Slack app's current
`conversations.history` rate-limit tier first — it decides whether live Slack
reads are worth building.

**Deep research.** A LangGraph flow in `app/research/`: plan (1 LLM) → gather
from graph + index + facts + live tools in parallel (0 LLM, bounded) →
synthesize (1) → audit (1), at most one refinement round. Hard budget per run
(O5); hitting it yields a partial report that says so. Runs as the asker's
`Viewer`, one scope. Reports saved like `scheduler_reports`.

**MCP server.** FastMCP adapter over `app/tools/` + walk + retrieval, per-user
OAuth 2.1 so every call resolves to a real `Viewer`, off by default per org
because results leave for a model Handbook's `data_collection: deny` cannot
govern.

---

## A question end to end (after Phase 1)

"Who should I talk to about the token-refresh bug?"

1. Linking: "token refresh" → `ENG-12` (title match).
2. Walk, filtered at every hop: `ENG-12` assigned to Priya; PR #14 references
   it and was reviewed by Rahul; Priya edited "Auth design" — included only if
   the asker can open that file.
3. Evidence chunks join vector + BM25 (+ recency) in RRF; the gate and strict
   prompt run unchanged.
4. One generation call; every block names its source.

---

## Security test checklist (required per phase)

- [ ] Workspace ↔ org ↔ other workspace: no rows cross in either direction
- [ ] Private Drive file: its edges, node names and aliases hidden from non-viewers
- [ ] A walk through a hidden node reveals nothing past it; counts use visible edges only
- [ ] Revocation: unsharing hides dependent edges on the next walk
- [ ] Private Slack channel membership visible only to members
- [ ] Slack channel reply: `public_only` graph, no personal memory
- [ ] Disconnecting a source purges its graph rows
- [ ] Same display name never merges two identities; cross-org email never links
- [ ] Non-public answers are never written to `query_answer_cache` (`_is_cacheable`); ACL-hashed keys only if 1c is revived
- [ ] Personal memory never contains document-derived text, never visible to another user

---

## Deliberately not built

- Neo4j or any second database; Redis as a source of truth.
- A Redis/Valkey cache tier, for now (Phase 1c deferred, 2026-09-25).
- A graph built from conversations; per-user copies of the graph.
- Chunks as graph nodes (they are evidence pointers only).
- A graph UI.
- Admin confirmation for identity links (v1).
- Write tools, or vendor MCP servers used as a client.
- GraphRAG-style whole-corpus extraction and community summaries.

## References

- Glean knowledge graph (content / people / activity; permissions and lineage on every edge; per-user GitHub OAuth for identity): https://www.glean.com/resources/guides/glean-knowledge-graph, https://docs.glean.com/connectors/native/github/about
- Microsoft 365 Copilot semantic index ("does not create new access"): https://learn.microsoft.com/en-us/microsoftsearch/semantic-index-for-copilot
- Zep / Graphiti temporal knowledge graph and entity resolution: https://arxiv.org/abs/2501.13956
- LightRAG vs GraphRAG indexing cost and incremental updates: https://learnopencv.com/lightrag/
- Onyx removing its unused KG extraction pipeline: https://github.com/onyx-dot-app/onyx/pull/14780
- 37signals Solid Cache (database-backed cache in production): https://dev.37signals.com/solid-cache/
