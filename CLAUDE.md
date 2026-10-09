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
  A number is kept only when a document sat at that block. A live read of an
  indexed document is that document, and a GitHub live read uses the address
  the API returned. Attachments and graph facts are not citable. Numbers are
  renumbered per document.
  The link is `documents.source_uri` from sync (Notion page, Drive file, Slack
  thread, Linear issue), http(s) only, never a URL the model wrote.
- The web chat shows **one source as a single line under the answer**. Two or more
  sources keep a gray superscript plus that list (`frontend/components/AnswerText.tsx`).
  The `cited` list is stored on the turn and sent again when that chat is opened.
  So is the chart (`conversation_turns.chart`), as drawn: a snapshot, not a spec,
  so a reopened chart never disagrees with the answer beside it.
  So is who answered (`conversation_turns.meta`: source, agent, tools, files, live
  reads, model, a passage COUNT), written by the `done` event; never the passages.
  Slack strips the markers.
- Audit and moderation judge the answer with the markers removed.
- A link in an answer survives only if it appeared verbatim in the text the model
  was shown (`security/links.py`).

## 5. Gotchas

Each of these shipped a real bug. The fix is the rule.

**Retrieval.** A store fake must accept `viewer=`. `Viewer.acl()` lowercases every
entry. `doc_viewers && '{}'` is false, which is what makes public-only work.
`Viewer(email=None)` is unrestricted; do not conflate it with public-only.
A tool the question NAMES decides routing: one named tool is routed there;
several are probed among themselves and the connected answer reads exactly those,
never a tool the asker did not mention. Adjacent markers citing one document
collapse to one. A successful ingest clears that org's `query_answer_cache`. The bot's own Slack
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

**Charts.** Numbers come from SQL over stored rows (`activity_facts`, or a
document table in `doc_tables`/`doc_table_rows`). The model never emits a number,
an axis or a date: it fills in a spec, and code validates it twice (resolver and
store) and refuses rather than corrects. Metric fragments must not contain `{`,
`%` or `;`. Only our own identifiers are spliced into SQL (`registry.DIMENSIONS`,
attribute keys matching `fields.KEY_RE` — normalized at write, checked again at
read — and table column keys `c0`…); every value, including a filter the asker
typed, is resolved against real rows and bound. Facts keep every SIMPLE field a
source returned (`insights/fields.py`: no bodies, ids, links, timestamps or
emails; ≤40 keys) and the chartable set is DISCOVERED per scope and viewer
(`insights/attr_catalog.py`); `registry.ATTRS` is display hints only, never a
list of what may be charted. Chat and Slack pass a
spec through `resolve.spec_to_dict`, never a hand-built dict (that dropped `focus`).
Charts are built only in Chart mode (chat sends `mode: "chart"`; Slack: a question
the question check reads as a chart ask). Chart mode answers with a chart or a
refusal, never a document answer. Ask never runs the chart classifier: its question
check (`resolve.classify_route`) decides a live GitHub read, `needs_live` and
`chart_ask` ("visual" gets the Chart-mode hint, "count" the Turn on Chart button).
**No word list decides intent anywhere in charts**: the model reads the question
and code verifies what it can (a quote is in the question, a value has rows, a type
came from the source). Format parsers (₹/lakh/k/%) and API enums are not intent.
`ModelChoice.charts` marks the catalogued models that build charts.
The prompt says what `actor` and `subject` ARE in each tool (Linear: assignee,
team; `registry.ACTOR_LABELS`/`SUBJECT_LABELS`); bare keys sent "by team" to
Linear's `project` field. An empty value is named in the tool's terms
(`BLANK_ACTOR`: a Linear issue with no assignee is "Unassigned"), never blamed
on indexing. The prompt also lists the REAL subject and person names with activity
(`store.scope_names`, viewer-filtered, top 15): a focus is one of them, and
"our team" names nothing. A Chart-mode reply that is not a chart is badged
"Charts", never "No answer found"; an upload's table reports `attachment`.
A trend with fewer than `MIN_TREND_BUCKETS` periods on its drawn axis steps to a
finer period and says so; activity in one period only is said plainly.
"Done vs remaining" is the derived `progress` dimension (Open/Closed from the
source's state type; `registry.DERIVED_DIMENSIONS`, fixed SQL, kept apart from
the bare-column `DIMENSIONS`). In a TABLE, a time scale IS a breakdown by its
date column; a line with no breakdown uses the table's only date column.
Every chart says what its numbers ARE (`insights/describe.py`): a title naming
the measure ("Total Salary by Team", "Files created or edited: number of
different people"), the unit from the column header's bracket or the cells'
symbol written against the number ("₹130 lakh"; `withUnit` mirrors
`format_value`), and an `explain` line on what each bar/point is and over which
rows. Built from the measure, header, unit, breakdown and period, never copy.
A breakdown beyond the two a chart draws is named as left out
(`left_out_words`, kept only when the quote is in the question), never dropped silently.
A breakdown is kept only when asked for: the model quotes the words that asked
(`breakdown_words`) and the quote must be in the question, or the breakdown's own
label from the data must be; otherwise it is dropped (`resolve._honour_breakdown`).
Document tables are offered by the similarity of their DOCUMENT to the question
(`tables.document_similarity`), never by word overlap; the gate orders them and
never removes one (a table-heavy page embeds as numbers). A one-time data fix goes
in `schema.sql` behind a `schema_marks` row, never as a bare UPDATE.
Chart-mode starters come from the scope's metrics and discovered fields
(`GET /chat/chart-starters`), never page copy.
"Open"/"closed" on a state filter is a GROUP of real states (not finished / finished),
`query.AnyOf`, compiled to `= ANY(...)` and still bound; an exact state wins first.
"Finished" is the SOURCE's own state type (`attrs.state_type`, `store.state_types`),
never a list of state names; with no types, "open" is refused naming the real states.
The prompt maps the asker's wording to the two tokens; code matches no word lists.
A document table is exactly as visible as its document: offered only through the
visibility predicate, re-checked at run time, and never offered without a viewer.
Document tables are filled by dataset adapters (`doctables/base.py`); each
adapter replaces only its own `origin`. Ingestion never calls a model for
charts: an AI adapter (`background = True`) only enqueues, and the tick reads
`doc_text_queue` within the background budget. A figure read from prose is kept
only when its quote is in the document and every cell is in its quote; a
mismatch is dropped, never repaired, and the chart says "taken from text".
`DOCTABLES_TEXT_ENABLED` stays off unless background quota is budgeted for it.
A sync skips unchanged documents, so `documents.tables_checked_at` marks what the
adapters have read; `backfill_tables` re-fetches a bounded batch of unchecked
ones per sync (tables only, no re-embedding) and a failed fetch stays unchecked.
A table in an UPLOADED file hangs off `doc_tables.attachment_id` (never a
document) and is its uploader's, in that chat: offered and re-checked only with
`doctables.store.UploadScope` (conversation + user from the session), never by
`list_tables`, and it cascades with the attachment. Tables are read at upload
with no AI and never fail the upload; a table-less upload is read for figures
once, in Chart mode (`figures_read_at`). Workbooks are read by ONE reader
(`attachments.extract.xlsx_sheets`) for both the prompt text and the rows.
A Notion `table` renders as a pipe table with a header separator (first row =
header); bare "a | b" rows were never found by the table reader. An inline
database (`child_database`) renders as a table of its rows' properties under its
title (`_database_lines`, ≤1000 rows, either Notion API), never skipped.
A Sheet embeds a description of its columns, never its figures. An unparseable
cell is absent from a sum, never zero. A trend draws every period from "measured since"
(or the window, if later) to now, a quiet one at zero; never a period before the
data begins, which is unknown, not zero (`insights_agent._fill_gaps`). `points: null` means the panel failed; `[]`
means it ran and was empty. There is no `space` dimension. Sentiment is
owners-only with a floor of 5, admits no split/filter/measure, and Forms
responses are never indexed.

**Runtime.** Import heavy libraries inside functions. Chunking uses the heuristic
counter, not the BGE tokenizer. `CHUNK_MAX_CHARS=4000`. The browser calls this
origin at `/api`; Next rewrites to FastAPI. Do not point `NEXT_PUBLIC_API_BASE_URL`
at Render. `render.yaml` does not configure production Hand-Book. Supabase uses the
session pooler (5432).

**Also hold.** GitHub embeds nothing. A live read runs after the gate, at most two
items, and withholds the indexed copy only on not-found or permission. The "live" chip is shown only for a
live read the answer cites (`api/chat.py::_shown_live`). Personal
memory is written only from the asker's own question, is never evidence, and is
web chat only. A confirmed email change keeps the old address as an alias.
`GOOGLE_GROUPS_ENABLED` stays off unless the connecting account is a Workspace
admin; a missing optional scope must not fail the Drive connect. Indexed reports
describe current content, never a diff. `sync_requested_at` is a flag, not a queue.
Removing an attachment is idempotent (`deleted: false` for one already gone,
never a 404) and answers on the row delete; the object-store purge runs after
the response. The injection guard logs questions and never refuses them. A scrubbed-to-empty
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
app/insights/        charts: registry, query grammar, document-table picks
app/doctables/       tables inside documents, kept as typed rows for charts
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
