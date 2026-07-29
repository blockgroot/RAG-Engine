# Google Drive + Google Docs integration — implementation plan

> Branch: `feature/google-integration`, based on `main` @ `876d6f8`.
> Status: **Phases 1–7 implemented** (provider partition, token refresh,
> Google OAuth, folder config API, Drive adapter, end-to-end wiring, frontend).
> Phase 8 live OAuth walkthrough still pending (needs a real internal-use
> Google Cloud OAuth client). Companion rulebook: `CLAUDE.md`.

---

## 1. Understanding summary

- **What.** A second external content source alongside Notion: an org's admin
  connects Google Drive via per-org OAuth, names one or more Drive folders, and
  the existing ingestion pipeline pulls the **native Google Docs** in those
  folders into the same chunk/embed/store path Notion already uses.
- **Why.** Companies keep policies in Drive/Docs as often as in Notion. The
  `SourceAdapter` interface was built for exactly this (its docstring already
  names "Google Drive/Docs/Sheets" as the next target). Nothing about
  retrieval, grounding, or the gate changes.
- **Who for.** Existing tenants (org admins connect; employees just ask
  questions and get answers grounded in whichever sources are connected).
- **Key constraint.** Strict multi-tenant isolation must hold, and a Google
  connection must be able to **coexist** with a Notion connection in the same
  org without either clobbering the other's documents.
- **Non-goals this stage.** Google Sheets, Drive-hosted PDF/DOCX (layout-aware
  extraction stays deferred per CLAUDE.md §4), Slides, write access, a Google
  Picker UI, Google-based *login* (SSO), and public Google app verification.

## 2. Decisions (with alternatives and why)

| # | Decision | Alternatives considered | Why this |
|---|---|---|---|
| D1 | Base on `main` @ `876d6f8` | `feature/frontend-develop` | The 14 commits (incremental sync, in-API worker, Sources UI) landed in `main` via PR #9. `main` is now the real current system. |
| D2 | **Partition sync state by provider** before anything else | Ship Google first, fix later | Not optional — see §3. Today a Google sync would delete every Notion document in the org. Data loss. |
| D3 | Per-org **OAuth only** (`oauth_connections`), no env-var token path | Mirror Notion's `NOTION_TOKEN_<NAME>`; service account + domain-wide delegation | OAuth is the direction of travel (Phases 10–14). A second non-fallback credential path is complexity with no current consumer — YAGNI. Service accounts need Workspace super-admin setup and a different trust model. |
| D4 | Scopes `drive.readonly` + `documents.readonly`; deployment model = **internal-use OAuth client per tenant** | `drive.metadata.readonly` (min-privilege); `drive.file` + Picker; public app + CASA | `drive.metadata.readonly` is **also RESTRICTED**, so min-privilege buys zero verification relief. `drive.file` almost certainly doesn't grant folder children (unverified, no Google doc supports it). Google **exempts internal-use apps** from verification, CASA, the 100-user cap, and the 7-day refresh expiry — which is exactly the self-hosted enterprise model in CLAUDE.md §1. Code is identical if a public client is verified later. |
| D5 | **Native Google Docs only** in v1 | + Sheets, + PDF/DOCX | Docs is where prose policies live. PDF/DOCX needs layout-aware extraction, explicitly deferred in CLAUDE.md §4; pulling it forward widens this work a lot. |
| D6 | Extract via `files.export?mimeType=text/markdown` | `documents.get` + custom recursive renderer; export-with-fallback | Export returns ready-to-chunk Markdown, which `preprocessing.py` + `chunking.py` already assume. No 300-line parser to maintain. Costs no extra scope given `drive.readonly`. Accepted risks: 10 MB response cap, and multi-tab behaviour is unverified (§7 R2). |
| D7 | Admin **pastes a Drive folder URL/ID**; stored per connection | Google Picker JS; ingest whole Drive | Zero new frontend dependencies (the frontend has no UI/SDK deps at all). Mirrors Notion's "explicitly share the page with the integration" — the boundary stays externally enforced by Google. Whole-Drive would drag an admin's personal files into the policy corpus. |
| D8 | Folder config in a **JSON `source_config` TEXT column** on `oauth_connections` | New `connection_folders` table; JSONB | One column, no new table, no new FK. Precedent already exists: `conversation_last_retrieval.chunks` stores a JSON array in `TEXT`. |
| D9 | Call Drive/Docs **directly with `httpx`** | `google-api-python-client` + `google-auth` | Same reasoning as plain-OpenAI-over-LiteLLM and notion-client-over-llama-index (CLAUDE.md §1/§2): those libs pull dozens of transitive deps to wrap REST calls we can make in a few lines. **Zero new Python dependencies.** |
| D10 | Implement **token refresh** in the credentials layer, provider-agnostic | Refresh inside the adapter; ignore expiry | Google access tokens expire in ~1 h; `OAuthProvider.refresh()` exists but has **never been called** anywhere in this codebase. The credentials layer is the one place that already owns encrypt/decrypt + the `(org_id, provider)` row, so refresh belongs there and every caller benefits. |

## 3. The blocking defect this plan must fix first

`documents` carries `source_external_id` / `source_last_modified` but **no
provider column**. Sync state is keyed on `(org_id, source_external_id)` only,
and both `detect_source_changes()` and `ingest_source()` read
`store.list_source_documents(org_id)`, which returns rows for **every**
provider in that org.

So the first Google sync in an org that also has Notion computes:

```
removed = stored_external_ids − google_live_ids     # = every Notion page id
store.delete_source_documents(org_id, removed)      # chunks cascade
```

→ **every Notion document and its chunks are deleted.** Symmetrically, the next
Notion sync would delete every Google document. `GET /admin/connections/{id}/changes`
reports the same nonsense (`removed_count` = the other provider's whole corpus).

Phase 1 exists solely to close this, and no Google code lands before it is green.

## 4. Phase plan

Each phase: small, independently testable, own commit(s), suite green before the
next. `→` marks the dependency.

### Wave A — foundations (3 phases, parallelisable)

**Phase 1 — Partition sync state by provider.** *(blocks P5–P8)*
- `schema.sql`: `ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_provider TEXT;`
  backfill `UPDATE documents SET source_provider = 'notion' WHERE source_provider IS NULL AND source_external_id IS NOT NULL;`
  replace the unique index with `(org_id, source_provider, source_external_id) WHERE source_external_id IS NOT NULL`
  (drop the old one by name; all DDL idempotent, per convention).
- `VectorStore` + `PgVectorStore`: thread a required `provider` through
  `list_source_documents(org_id, provider)`, `upsert_source_document(..., provider=...)`,
  `acknowledge_source_document(..., provider=...)`, `delete_source_documents(org_id, provider, external_ids)`.
  `StoredSourceDocument` gains `provider`.
- `pipeline.py`: `ingest_source(..., provider: str)` and
  `detect_source_changes(..., provider: str)` — plumbed from the caller's
  connection, never guessed.
- Callers updated: `app/jobs/worker.py`, `app/api/admin.py`, `scripts/ingest_notion.py`.
- **Tests:** extend `tests/test_incremental_sync.py` — a Notion sync and a
  Google sync in the same org must each see only their own rows, and
  `removed_count` must be 0 for the other provider's documents. This is the
  regression test for the §3 defect.
- **Note:** `upsert_source_document` is delete-then-insert, so a document's UUID
  changes on every update. Unchanged by this phase, but worth knowing.

**Phase 2 — Provider-agnostic token refresh.** *(independent)*
- `app/auth/credentials.py`: add `get_live_connection_token(org_id, provider) -> str`
  — read `access_token_encrypted`, `refresh_token_encrypted`, `expires_at`; if
  `expires_at` is within a safety margin (5 min) call
  `build_oauth_provider(provider).refresh(...)`, re-persist, return the fresh
  token. `NotImplementedError` (Notion) → return the stored token unchanged.
  `invalid_grant` → raise a distinct terminal error so the job/endpoint fails
  with an actionable "reconnect Google Drive" message; never retry-loop.
- Callers switch from `get_connection_token` to the new function:
  `app/jobs/worker.py`, `app/api/admin.py` (both the changes and ingest paths).
  Keep `get_connection_token` as the raw accessor.
- **Tests:** new `tests/test_token_refresh.py` — expired token refreshes and
  re-persists; unexpired token doesn't call the network; Notion
  (`NotImplementedError`) passes through; `invalid_grant` is terminal. Fake the
  provider, monkeypatch `httpx.post` at the module path (the established idiom).

**Phase 3 — `GoogleOAuthProvider`.** *(independent)*
- `app/config/settings.py`: `GoogleSettings` (frozen dataclass + `from_env()`,
  the only place env is read) — `client_id`, `client_secret`, `redirect_uri`,
  `scopes` (default `drive.readonly documents.readonly`).
- `app/auth/google_oauth.py`: `authorize_url(state)` →
  `https://accounts.google.com/o/oauth2/v2/auth` with `response_type=code`,
  space-delimited `scope`, `access_type=offline`, `prompt=consent`,
  `include_granted_scopes=true`, `state`. `exchange_code(code)` → **form-encoded**
  POST to `https://oauth2.googleapis.com/token` (Notion uses JSON + basic auth —
  not reusable). `refresh(refresh_token)` implemented (the first real
  implementation in this codebase). Populate `OAuthTokens.expires_at` from
  `expires_in`.
- `external_workspace_id` is `NOT NULL`, so derive identity from
  `GET https://www.googleapis.com/drive/v3/about?fields=user(emailAddress,displayName,permissionId)`
  — avoids adding `openid`/`email` scopes or decoding an id_token.
- Verify the **granted** `scope` in the token response is a superset of what we
  need (Google may grant a subset) and fail the connect with a clear message if not.
- `app/auth/factory.py`: `elif provider == "google"`; update the error string;
  export from `app/auth/__init__.py`.
- **Tests:** new `tests/test_google_oauth.py` mirroring `tests/test_auth.py`'s
  pattern (env-var fixture + `monkeypatch.setattr("app.auth.google_oauth.httpx.post", ...)`).
  **Also update `tests/test_auth.py:61`**, which currently asserts
  `build_oauth_provider("google")` *raises*.

### Wave B — the Google source (2 phases)

**Phase 4 — Per-connection folder configuration.** *(→ P3)*
- `schema.sql`: `ALTER TABLE oauth_connections ADD COLUMN IF NOT EXISTS source_config TEXT;`
- `app/auth/credentials.py`: `get_source_config(org_id, provider) -> dict` /
  `set_source_config(org_id, provider, config)`. `save_connection` must **not**
  clobber `source_config` on re-connect (the upsert currently rewrites the row).
- `app/api/admin.py`: `PUT /admin/connections/{connection_id}/config`, body
  `{"folder_urls": [...]}`. Parse Drive folder URLs → ids server-side, then
  **validate each** via `files.get` (accessible? actually a folder?) and return
  resolved folder names so the admin gets immediate feedback. `GET` returns the
  current config. Org-scoped from the session, like every other admin route.
- **Tests:** extend `tests/test_api_admin.py` — URL→id parsing across Drive's
  URL shapes, cross-org 404, invalid/inaccessible folder → 400, config survives
  a re-connect.

**Phase 5 — `GoogleDriveAdapter`.** *(→ P1, P4; the largest phase)*
- `app/sources/google_drive.py`, implementing the unchanged `SourceAdapter`
  contract. All conversion stays inside the adapter (CLAUDE.md §2).
- `list_documents()` — BFS walk from the configured folder ids. Per level:
  `GET /drive/v3/files?q=<parents OR'd> and (mimeType='application/vnd.google-apps.document' or mimeType='application/vnd.google-apps.folder') and trashed=false`
  with explicit `fields=nextPageToken,incompleteSearch,files(id,name,mimeType,modifiedTime,webViewLink,parents,trashed,shortcutDetails)`
  (Drive's default projection omits `modifiedTime` — omitting `fields` silently
  breaks incremental sync), `pageSize=1000`, `supportsAllDrives=true`,
  `includeItemsFromAllDrives=true`. `'<id>' in parents` is **not recursive**, so
  the walk is ours: visited-set for cycles, depth bound (Drive's own limit is
  100), OR'd parents batched (~25/query) to cut round trips, resolve
  `application/vnd.google-apps.shortcut` via `shortcutDetails.targetId`, log
  `incompleteSearch=true` rather than silently under-ingesting. Returns
  `SourceRef(external_id=file id, title=name, last_modified=modifiedTime, source_uri=webViewLink)`.
- `fetch_document()` — `GET /drive/v3/files/{id}/export?mimeType=text/markdown`
  → `SourceDocument`. `>10 MB` (Google's documented export cap) → `SourceError`
  naming the document, so one huge doc fails visibly instead of corrupting the corpus.
- `get_last_modified()` — `files.get?fields=modifiedTime`.
- Shared retry/backoff helper: truncated exponential backoff with jitter on
  **429 and 5xx**, and on **403 only when `reason` is a rate-limit reason**.
  Error mapping: 401 → token/scope problem (Google overloads 401 with
  insufficient scope, so don't blindly refresh-loop); 403
  `ACCESS_TOKEN_SCOPE_INSUFFICIENT` → terminal, needs re-consent; **404 →
  "no longer accessible"**, because Drive deliberately returns 404 (not 403) for
  files a token can't see, making it indistinguishable from deletion. A
  subfolder 404 skips that subtree and continues; the *root* folder 404 fails
  the job with an actionable message.
- Every failure wrapped as `SourceError(..., cause=exc)`; missing config →
  `ConfigurationError`. Lazy imports and the same three-method shape as
  `notion.py`.
- Empty docs need no special handling — the pipeline's
  `acknowledge_source_document` path already covers them.
- **Tests:** new `tests/test_google_drive.py` — offline, monkeypatching the
  adapter module's `httpx`. Cover: folder walk collects nested Docs and skips
  folders; pagination followed; trashed excluded; shortcut resolved; `fields`
  includes `modifiedTime`; markdown export → `SourceDocument`; oversize export →
  `SourceError`; 404 mid-walk skips a subtree; 429 retried then succeeds. Plus
  the duck-typed fake-adapter pattern from `tests/test_incremental_sync.py` for
  a full `ingest_source` round trip.

### Wave C — wiring

**Phase 6 — Wire Google end to end.** *(→ P1–P5)*
- `app/sources/factory.py`: `elif source_type == "google"` →
  `GoogleDriveAdapter`; add a `config: dict | None = None` param so callers pass
  the resolved folder config; update the error string and the Notion-token-specific
  docstring. Export from `app/sources/__init__.py`.
- `app/jobs/worker.py` and `app/api/admin.py`: fetch `source_config` alongside
  the token and pass both. The worker's provider string already comes from the
  DB, so no dispatch logic changes.
- `app/api/admin.py`: catch `ConfigurationError` in the changes endpoint — today
  an unknown provider **500s** there instead of returning 400.
- **Tests:** extend `tests/test_jobs.py` (a Google job builds a Google adapter
  with its config) and `tests/test_api_admin.py` (changes endpoint works for a
  Google connection; missing folder config → actionable 400, not a 500).

### Wave D — product surface

**Phase 7 — Frontend.** *(→ P4, P6)*
- `frontend/components/ConnectionCard.tsx:37` — widen the single availability
  gate `provider === "notion"`. `PROVIDER_LABELS` already has
  `google: "Google Drive"`.
- Replace hardcoded "Notion" copy with a per-provider copy map keyed like
  `PROVIDER_LABELS`: `ConnectionCard.tsx:112`, `admin/connections/page.tsx:28,30,166`,
  `chat/page.tsx:129` ("Finish connecting Notion…" shown to a Drive-only org today).
- Folder configuration UI on the Sources page: paste-a-folder-URL field per
  Google connection, showing resolved folder names, calling the Phase 4 endpoint.
- Generalise `frontend/app/onboarding/page.tsx` — currently hardwired to Notion
  (`connections.find(c => c.provider === "notion")`, `api.connectUrl("notion")`,
  and a `?connected=` handler that always says "Notion connected" regardless of
  the actual provider). Google must be a valid *first* connection.
- Post-OAuth landing: the backend always redirects to
  `/onboarding?connected={provider}`, so connecting Drive from the Sources page
  currently bounces to onboarding. Route by setup state.
- `api.ts`, `routing.ts`, `AppShell.tsx`, `useMe.ts` need **no** changes
  (all provider-agnostic). No new npm dependency.
- **Verify:** `npm run build` clean.

### Wave E — proof and documentation

**Phase 8 — Live verification, eval, docs.**
- Full suite green (`pytest`, plus `-m "not network"` for the CI fast tier).
- **Live walkthrough** against a real internal-use Google Cloud OAuth client:
  connect → name a folder → sync → ask a question → answer grounded in a Doc
  with a working `webViewLink` citation → edit the Doc → change-check shows
  1 updated → re-sync → no duplicates. Then the coexistence test that matters:
  **an org with both Notion and Google connected, syncing each in turn, with
  neither corpus losing a document.**
- **Verify the two open risks** (§7 R2/R3) with real documents and record the
  findings in CLAUDE.md §4 — the project's standing habit for empirical results.
- Extend `evaluation/golden_set.py` with a Google-sourced case if the corpus
  convention allows it without breaking CI determinism.
- Update `CLAUDE.md`: §2 (the decisions above), §3 (new files), §4 (gotchas:
  provider-partitioned sync, Google's 404-means-invisible, the internal-use
  deployment model, restricted-scope reality), §5 (`documents.source_provider`,
  `oauth_connections.source_config`), §6 (built vs pending). Update
  `ARCHITECTURE.md`, `.env.example` (`GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
  / `GOOGLE_REDIRECT_URI`), and `README.md` setup steps.

## 5. What deliberately does not change

The gate, the grounded prompt, retrieval (hybrid + RRF + rerank), memory,
retrieval reuse, recovery, web-search fallback, `PolicyAgent`, and the chat/SSE
path are all **untouched**. Google is a new `SourceAdapter` behind the existing
interface plus a second `OAuthProvider` — exactly the extension shape both
abstractions were built for. `RAG_SIMILARITY_THRESHOLD` needs no recalibration:
Google content becomes ordinary chunks in the same `chunks` table.

## 6. Parallel execution strategy

Dependencies allow three waves of concurrency. Shared files are the real risk,
so each wave assigns exactly **one owner per shared file**:

| Wave | Parallel phases | Shared-file owner |
|---|---|---|
| A | P1, P2, P3 | `schema.sql` → P1. `settings.py` → P3. `credentials.py` → P2. `admin.py` → P2. |
| B | P4, P5 | `schema.sql` → P4. `admin.py` → P4. P5 touches only new files + tests. |
| C | P6 alone | integrates; resolves any residual drift. |
| D | P7 alone | frontend only. |
| E | P8 alone | docs + live verification. |

Each phase agent: implement → run the phase's tests → run the full suite →
commit with a message following the repo's style. Waves are gated: the next
wave starts only when the previous wave's commits are on the branch and green.

## 7. Risks and open items

| | Risk | Handling |
|---|---|---|
| R1 | The §3 provider-partitioning defect destroys a live corpus | Phase 1 first, with an explicit coexistence regression test. Nothing Google-facing merges before it. |
| R2 | **Unverified:** does `files.export?mimeType=text/markdown` return *all* tabs of a multi-tab Doc, or only the first? (`documents.get` returns only the first unless `includeTabsContent=true`) | Test with one real multi-tab Doc in Phase 5. If it flattens to tab 1, add the `documents.get` renderer as a fallback — a contained, known-cost change (D6's rejected third option). |
| R3 | **Unverified:** whether `files.list` includes trashed items by default | Always pass `trashed = false` explicitly. Already in the Phase 5 query. |
| R4 | Google grants a *subset* of requested scopes | Phase 3 checks the granted `scope` in the token response and fails the connect with a clear message. |
| R5 | Refresh tokens die (revoked, 6-month idle, password change, 100-token-per-account cap evicting the oldest) | Phase 2 treats `invalid_grant` as terminal with a "reconnect" message; never retry-loops. |
| R6 | Testing-mode OAuth clients expire refresh tokens after **7 days** (still true in 2026) | Exactly why D4 specifies internal-use clients, which are exempt. Must be called out in the setup docs, or ingestion silently breaks weekly. |
| R7 | Drive/Docs quota (Drive 325k units/min/user; Docs 3 000 reads/min/project) | Backoff helper in Phase 5; batched OR'd parent queries reduce call volume. |
| R8 | One `oauth_connections` row per `(org_id, provider)` → an org cannot connect two Google accounts | Accepted for now. Lifting it means a schema change and a per-connection provider identity, well beyond this scope. |
| R9 | `upsert_source_document` is delete-then-insert, so `document_id` changes on every update | Pre-existing; nothing here depends on a stable document UUID. Flag it if citation permalinks are ever built on it. |

## 8. Also noticed (out of scope, worth a separate PR)

- `0243bfc` "Implement conversation cleanup and retention policy" exists **only
  locally** on `feature/frontend-develop` — not pushed, not in `main`.
- `app/db/schema.sql:155` still claims ingestion jobs are consumed by a worker
  "**not** an in-process background task" — stale since the in-API worker landed.
- `pyproject.toml` `[project].dependencies` has drifted from `requirements.txt`
  (missing `fastapi`, `httpx`, `cryptography`, `pyjwt`).
- `frontend/components/Nav.tsx` looks like dead code superseded by `AppShell`.
- `tests/fakes.py`'s `RecordingVectorStore` / `TopicAwareVectorStore` don't
  implement the four incremental-sync methods, so `ingest_source` has no
  end-to-end fake-store test today.
