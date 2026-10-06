# CLAUDE.md — constraints

Standing rules for every change. Feature status and the plain-language walkthrough
live in `docs/handbook/`. History lives in git. Add a line here only when a new
**constraint** appears; ship notes go in `docs/handbook/PRODUCT_STATUS.md`.

## 1. What this is

A multi-tenant RAG platform: a company connects Notion, Drive, Slack, Linear and
GitHub; employees get answers from *their* data.

- RAG, not fine-tuning. Facts change; re-ingest.
- One org must never see another's content.
- Prefer pieces that run locally. The goal is a self-hosted image.

## 2. Conventions

- **New capability = new package:** `base.py` + impl + `factory.py` + `__init__.py`.
  Depend on the interface and `build_*()`, never a concrete class.
- **An orchestrator skips `base.py`** (`app/rag/`, `app/ingestion/`, `app/schedulers/`,
  `app/graph/`, `app/insights/`). Do not add one speculatively. `app/agent/` has a
  `base.py` because there is one agent per source.
- **All config** is a frozen dataclass with `from_env()` in `app/config/settings.py`.
  Nothing else reads the environment. Each factory calls `X.from_env()` itself.
- **All failures** raise `ProviderError` or a subclass (`app/core/exceptions.py`)
  with `cause=` and `raise ... from`.
- `from __future__ import annotations`. Docstrings say why.
- **Bound every external walk and mark truncation.** A partial result that looks
  complete is the failure that matters.
- **`org_id` enters a request in one place:** `app/api/deps.py`, from the signed
  session cookie. Never from client input.
- **Prompts that carry outside text** put `UNTRUSTED_POLICY` before the fence and
  `UNTRUSTED_REMINDER` after it (`app/security/untrusted.py`). The wording is
  `app/security/agents.md`. `tests/test_untrusted_policy.py` fails if a prompt skips it.

## 3. Isolation and access

- `org_id` is on every tenant table. Every read and write filters on it in the
  query, before ranking. The vector index is not the isolation boundary
  (`tests/test_isolation.py`).
- `workspace_id` nests inside `org_id` and is never used alone. `NULL` is org-wide.
  A workspace sees only its own rows, never also the org-wide ones.
  `workspaces/store.py::assert_member` is the membership check. A non-member gets
  **403, never an empty result**.
- **Document access has one predicate** (`security/visibility.py`). Retrieval adds
  `AND (d.doc_is_public OR d.doc_viewers && acl)` in the same `WHERE` as `org_id`,
  on every leg, including charts, chips, digests and the graph walk. Entries are
  **emails**, lowercased. `Viewer.acl()` and `_normalize_viewers` must agree.
- **`Viewer` has three states.** `unrestricted()` is no filter (ingest, eval, CLI).
  A real email is that person. `public_only_viewer()` matches nothing private.
  A signed-in session whose user row cannot be read is public-only, never unrestricted.
- **Capture fails closed.** An ACL-capable provider that reports no sharing indexes
  the document for the connected account only. An already-indexed document whose
  sharing cannot be read keeps the viewers it had. Revocation replaces the set on
  the next listing; it does not union.
- Slack: a public channel is scope-public; a private channel's members are its ACL,
  plus `channel:<id>`. A channel reply is public-only and carries exactly that
  channel. Linear: a team's membership is its issues' ACL. GitHub: a private repo
  answers only an asker whose linked login can open it.
- A conversation is personal (`conversations.user_id`). `query_answer_cache` stays
  keyed on the scope and the model, not the asker. Do not store an answer when any
  hit was non-public or the reply is an access notice.
- Notion has no per-page permission API. Say so on the Sources card. A half-enforced
  guarantee that reads as whole is worse than none.

## 4. Grounding and citations

- Two layers, both stay: the **0.35 cosine gate** refuses without calling the model,
  and the strict prompt refuses when the passages do not answer. Do not raise the
  gate. Do not feed RRF scores or reranker logits into it. Reuse stays at 0.72.
- **Citations** (`app/rag/cite.py`). The model may write `[n]` after a sentence.
  A number is kept only when a retrieved chunk sat at that block. Attachments, live
  blocks and graph facts are not citable. Numbers are renumbered per document.
  The link is `documents.source_uri` from sync (Notion page, Drive file, Slack
  thread, Linear issue), http(s) only, never a URL the model wrote.
- The web chat shows **one source as a single line under the answer**. Two or more
  sources keep a gray superscript plus that list (`frontend/components/AnswerText.tsx`).
  The `cited` list is sent on the finished-answer event and is **not stored** on the
  turn, so a reopened chat has no source list. Slack strips the markers.
- Audit and moderation judge the answer with the markers removed.
- A link in an answer survives only if it appeared verbatim in the text the model
  was shown (`security/links.py`).

## 5. Gotchas

Each of these shipped a real bug. The fix is the rule.

**Retrieval.** A store fake must accept `viewer=`. `Viewer.acl()` lowercases every
entry. `doc_viewers && '{}'` is false, which is what makes public-only work.
`Viewer(email=None)` is unrestricted; do not conflate it with public-only.
A successful ingest clears that org's `query_answer_cache`. The bot's own Slack
traffic is not indexed. Query-norm max edit distance is 1, and the normalized
string is never the web-search query.

**Model.** The grounded prompt's `MODE:` tag must be at the start of the reply or
the audit never runs. Do not move CONTEXT or QUESTION earlier in that prompt.
`LLM_AUX_BASE_URL` and `LLM_AUX_API_KEY` are both or neither. Interactive calls
are never throttled; background work keeps `LLM_RESERVE_RPM` free. A 429 is the
quota, not a blip. Do not name the routing wrapper in `build_aux_llm_provider`'s
docstring; a test reads that source.

**Sync.** Every sync path takes an explicit provider. Disconnect uses
`sources.factory.INDEXED_PROVIDERS`, not a hand-kept list. A Slack listing deletes
only what it could have listed. Drive and GitHub 404 means "not found or not
accessible", never "deleted". A connector that needs a folder or channel list must
show its picker before anything is configured. `last_sync_at` is stamped on attempt.
`needs_reauth` rows are not retried. A host that cannot refresh must not flag the
tenant's token (`ConfigurationError` re-raises).

**SQL and jobs.** `WHERE id IN (SELECT … SKIP LOCKED LIMIT n)` does not bound an
UPDATE; use a CTE. Cap requeue attempts. `ALTER TABLE … ADD COLUMN` follows that
table's `CREATE TABLE` in `schema.sql`. Partial unique indexes where `NULL` means
org-wide. `python -m app.db.migrate` does nothing; call `apply_schema()`.
`register_vector` runs once per physical connection. Every process calls `close_pool()`.

**Charts.** Numbers come from SQL over `activity_facts`. The model never emits a
number, an axis or a date. Metric fragments must not contain `{`, `%` or `;`.
`points: null` means the panel failed; `[]` means it ran and was empty.
There is no `space` dimension. Sentiment is owners-only with a floor of 5, and
Forms responses are never indexed.

**Runtime.** Import heavy libraries inside functions. Chunking uses the heuristic
counter, not the BGE tokenizer. `CHUNK_MAX_CHARS=4000`. The browser calls this
origin at `/api`; Next rewrites to FastAPI. Do not point `NEXT_PUBLIC_API_BASE_URL`
at Render. `render.yaml` does not configure production Hand-Book. Supabase uses the
session pooler (5432).

**Also hold.** GitHub embeds nothing. A live read runs after the gate, at most two
items, and withholds the indexed copy only on not-found or permission. Personal
memory is written only from the asker's own question, is never evidence, and is
web chat only. A confirmed email change keeps the old address as an alias.
`GOOGLE_GROUPS_ENABLED` stays off unless the connecting account is a Workspace
admin; a missing optional scope must not fail the Drive connect. Indexed reports
describe current content, never a diff. `sync_requested_at` is a flag, not a queue.
The injection guard logs questions and never refuses them. A scrubbed-to-empty
input stays empty.

## 6. Where things live

```
app/config/          the only env reads
app/api/             org_id enters here (deps.py)
app/rag/             query path, cite.py, the 0.35 gate
app/security/        visibility predicate, untrusted text, link provenance
app/sources/         Notion, Drive, Slack, Linear
app/githublive/      GitHub, live, no vectors
app/agent/           per-source agents and routing
app/insights/        charts from activity_facts
app/graph/           knowledge graph
app/livetools/       live re-reads
app/memory/          chats and personal facts
app/jobs/            ingest queue and automatic sync
app/db/schema.sql    tables
docs/handbook/       what is live, and a walk through each feature
ARCHITECTURE.md      how a request moves through the system
```

Product status: `docs/handbook/PRODUCT_STATUS.md`.
Feature walkthrough: `docs/handbook/current-features.md`.
