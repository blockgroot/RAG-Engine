# Plan: live connector access for the Second Brain (the live-tools gateway)

Status: **Phase 0 + Phase 1 in progress, revision 4** · Branch: `feat/live-tools` · Date: 2026-09-29
Owner: Second Brain · Related: `docs/plans/2026-09-23-second-brain.md`, `app/githublive/`

Revisions 2 and 3 fold in two reviews (§14 lists every change and why). The
short version: **the index finds, the live call refreshes** — for every
connector — and Phase 1 is **Linear only, with no extra model call**.

**Revision 4: live reads exist ONLY in deep research mode.** Normal search and
Q&A keep answering from the knowledge graph and the index exactly as today — no
live call, no handle, no prompt change, nothing to measure against the latency
budget. A member opts in per question with the **Deep research** toggle in the
composer (`deep_research: true` on `POST /chat/stream`); only then does the
gateway refresh what retrieval found. Slack has no deep research mode.

---

## 0. Summary

Today every answer except GitHub's comes from **our index**, at most one sync
interval (1h) old, holding only what ingestion stored (no ticket comments, no
status change since the last sync). This plan lets an answer use a connector's
**live API** at question time, under three non-negotiable requirements:

1. **Live** — the data is fetched from the provider when the question is asked.
2. **No token exposure** — the model never sees, receives or can obtain a
   provider token, URL or header.
3. **Permissions** — a live read may only return what (a) the company/space, (b)
   the connection's scope and (c) *this asker* may already see.

The mechanism is a **gateway** inside the backend (`app/livetools/`). The
central rule, which satisfies requirement 3 structurally rather than by a
second filter:

> **The live call only ever refreshes an object that retrieval already found and
> already cleared for this asker.** Discovery stays in the index (which applies
> the visibility predicate before ranking); the live call re-reads *that exact
> object* from the provider. The gateway never searches a provider.

```
question ──▶ routing + graph plan + retrieval (unchanged, viewer-filtered)
                 │  hits: [N1] Leave Policy (notion) · [L2] SYV-5 (linear) · …
                 ▼
            GATEWAY  (app/livetools/gateway.py, our server)
              1. pick refreshable hits (mode A: deterministic, no model call)
              2. handle → (provider, external_id) looked up SERVER-SIDE from `documents`
              3. connector connected in scope?          else skip
              4. decrypt token → call provider → discard token
              5. injection guard (enforce mode) + scrub + cap, truncation marked
              6. audit row
                 ▼
            live block ("Live from Linear, fetched 12:04") + indexed chunks
                 ▼
            grounded generation → answer audit (sees the live block) → link rule
```

---

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| D0 | **Live reads run only in deep research mode.** `POST /chat/stream` takes `deep_research`; the chat edge sets a request-scoped `LiveRequest` (`app/livetools/context.py`, the `GraphPlan` ContextVar pattern) and the pipeline refreshes only when one is set AND `LIVE_TOOLS_ENABLED` AND the org is allowed. Without it, every code path below is skipped and the answer is byte-identical to today. Slack never sets one. | Normal Q&A must not change, cost or slow down; a member who wants the current state of a ticket asks for it explicitly. It also keeps the blast radius of a new provider path to the people who chose it. |
| D1 | **The model never holds a token.** Only the gateway calls provider APIs. | A token the model never had cannot leak through output or prompt injection. |
| D2 | **Read-only, fixed operations; no generic HTTP tool; no search tool.** | A generic tool reaches everything the token can; a provider search bypasses our visibility predicate. Read-only removes the "injected instruction makes the bot act" class. |
| D3 | **The index finds, the live call refreshes — for every connector.** A live read targets only a document retrieval returned for this asker. | Permissions are inherited from retrieval (which filters by `org_id`, `workspace_id`, scope and `visibility_predicate` before ranking) instead of re-implemented per provider. It also removes Slack's user-token search problem and Drive's nested-folder walk. |
| D4 | **(Lands with mode B, Phase 2.) Opaque handles, resolved server-side.** Mode A needs none: the server picks the targets from this request's hits and resolves `(provider, external_id)` from the `documents` row itself, so no target is ever named by the model. Rendering handles into the prompt before a model can use one would be machinery with no reader. Every hit in the context carries a handle (`[L2]`, `[S3]`); anything that names a target passes the handle, and the gateway maps it to `(provider, external_id)` from the `documents` row of *this request's* hits. | The model never sees a channel id, thread ts or file id, so it cannot aim a call at something retrieval did not clear. Fixes the `list_reviews`-class bug (CLAUDE.md): a tool needing a value not in the context is unreachable. |
| D4a | **(With D4.) Handles never reach the user.** The model will sometimes echo them ("per [L2]…"). They are stripped from the final answer at the edge, in the same place the `MODE:` tag is parsed off, BEFORE the answer is streamed, cached, written as a turn, sent to Slack or logged as a gap. | A handle is machinery, meaningless to a reader and a leak of internal structure into the cache and Slack. |
| D4b | **(With D4.) Handles are minted for reused hits too.** Mode A already refreshes reused hits: it reads `document_id` off whatever hits the turn has, fresh or reused. `_try_reuse` answers a follow-up from the previous turn's chunks without retrieval; those chunks get handles exactly like fresh hits, from the same `documents` rows, and resolve only within this request. | Otherwise "any update on it?" after "what's the status of SYV-5?" can refresh nothing — the most natural follow-up to a status question. |
| D5 | **Phase 1 is mode A only, in deep research: graph/retrieval-guided refresh, zero extra model calls.** When the graph plan or the top hits contain a refreshable object, the gateway re-reads it in parallel with prompt assembly, and the live block supersedes the stale chunk. | Handles the headline case ("latest on SYV-5") with no latency from a second model round and no cost against the 15 rpm budget. |
| D6 | **Mode B (the model requests a live read) is offered only on a refusal / gate miss — where the web tool already sits — and in deep research.** Never on every question. Arguments are handles only. | Grounded generation does not call tools today (`generate_with_tools` is used only by attachment paging, the web decision, GitHub and schedulers); offering tools on every answer means a serial tool-choosing round on every question. On a refusal that round is already being paid for. Settles rev 1's open question. |
| D7 | **Scope is honest per connector.** Slack = connected `channel_ids`; Drive = connected `folder_id`; GitHub = authorized repos; **Linear = everything the token can see** (no team scope exists in `source_config`, `autosync.SCOPE_KEYS` has only google/slack); **Notion = pages shared with the integration**. Because of D3 the live read can never exceed what the index already exposes; no guard is claimed that does not exist. A Linear team picker is a separate, optional change. | A claimed guard that does not exist is worse than a stated gap (CLAUDE.md, document-level access). |
| D8 | **One access model with the index.** Per-person visibility is exactly what `visibility_predicate` already decided at retrieval — including Drive's **owner-only fallback** for unreadable sharing, not a separate "drop" rule. Freshness adds one check: if the live read shows the object is gone or no longer accessible to the connection (404/403), the stale chunk is withheld too ("not found or not accessible"). | Two rules would show the same person a document in a normal answer and not in a live one. |
| D8a | **A withheld chunk re-checks the gate only when it came from the GATE DOCUMENT; if nothing else then clears 0.35, the answer goes through `_gate_failed` with a "no longer available" notice.** `gate_score` is the best cosine over ALL first-stage candidates (up to ~30, `retrieval.py:159`), taken BEFORE reranking cuts to `top_k` — so the final hits cannot reproduce it, and "recompute over the hits that remain" would silently change the gate on every withheld chunk. Instead `RetrievalResult` gains `gate_document_id`: the document of the candidate that produced `gate_score`. (1) Nothing withheld ⇒ untouched. (2) The withheld document is NOT the gate document ⇒ `gate_score` is untouched; the gate was never earned by it, and the answer proceeds from the rest. (3) It IS the gate document ⇒ drop ALL of that document's chunks from the hits (a withheld issue withholds every chunk of it, not only the one refreshed) and set `gate_score` to the best cosine of the hits that remain. That is a max over at most `top_k` rather than ~30, so it can only come out LOWER than a full recompute would — the safe direction: it can turn an answer into a refusal, never a refusal into an answer. Below 0.35 ⇒ `_gate_failed`. **Every candidate `.score` is a real cosine, verified:** the vector leg selects `1 - (embedding <=> q)`, the keyword leg selects the SAME expression for each matched chunk (BM25 only re-orders them in Python, `pgvector_store.keyword_search`), `chunks.embedding` is `NOT NULL`, the graph leg is a vector search, and `_rrf_fuse` keeps each chunk's own `score` (RRF governs order only). So there is no BM25-only hit to skip today; the recompute still skips any hit whose score is `None`, and a test pins that every leg returns a cosine, so a future score-less leg cannot quietly enter the gate. The refusal says the matching item is no longer available (deleted, or access removed), naming the CONNECTOR and never the title (the `access_notice.py` rule). Carried on the result as a flag (`live_withheld`), so nothing matches on message text: not cached (D13). **Not logged as a documentation gap — deliberately.** Right for revoked access (the company did write it); debatable for a genuine deletion, which may be a real gap. We cannot tell the two apart: a 404 and Linear's `Entity not found` mean "deleted OR you can no longer see it" (§5 says "not found or not accessible" for the same reason). A false gap sends an admin to rewrite a document that exists; a missed one costs one row, and a deleted-and-still-needed item resurfaces as an ordinary refusal once the stale chunk is re-synced away. So skipping is the default, and it is a decision, not an omission. | Someone asking about SYV-5 after it was deleted must not get a bare "I don't know", which reads as "nobody ever wrote that down"; and a gate earned by a chunk we then withheld would let a question with no remaining grounding reach generation. |
| D9 | **Live text passes every check indexed text passes:** fenced + scrubbed (`security/untrusted.py`); **injection guard** under `GUARD_MODE=enforce`, one batched call over the live blocks like web snippets (shadow mode logs); the live block is part of the **`contexts` handed to `_audit_answer`** (prepended the way `extra_contexts` is), so a correct fresh claim is not downgraded to a refusal; the block is in the prompt, so it counts as shown text for the **link rule** in `security/links.py`. | A Linear comment is exactly where a planted instruction sits; an audit that cannot see the live block would reject the very freshness this feature adds. |
| D10 | **Slack live reads wait on the rate-limit tier.** Since mid-2025, commercially distributed apps outside the Slack Marketplace get `conversations.history` / `conversations.replies` at ~1 request/min and 15 messages per request; internal apps keep ~50/min. Handbook installs into other workspaces via OAuth, so it is almost certainly on the restricted tier. Phase 0 checks the tier (response headers on a real call, or a Slack support ticket). **Restricted ⇒ no Slack live reads until a Marketplace listing.** Ingestion is affected too, not just live reads: on the restricted tier every `conversations.history` / `.replies` call returns at most 15 messages however large `limit` is (`slack.py` asks for 200), so a sync pages 13× more calls at 1/min. That is its own follow-up, sized by the same tier check. | Both Slack live methods are exactly the throttled ones; a live read that takes a minute is not live. |
| D11 | **Drive: refresh-by-handle only.** `drive_read_file` for an already-indexed, already-visible file (from its handle). No `drive_search`. | Nested folders would need a folder walk per question; D3 makes it unnecessary and keeps the owner-only rule (D8). |
| D12 | **The gateway lives in the backend, not a separate proxy service; no `factory.py`.** It is an orchestrator over existing providers, like `app/rag/` (CLAUDE.md §2's exception). | The protection is the checks, not the process boundary. A second Render service costs free-tier hours and a network hop. |
| D13 | **Live answers are never written to the scope-wide answer cache** (`_is_cacheable`). | Time-sensitive and assembled for one asker. |
| D14 | **Every live read is audited** in `live_tool_calls` (who, scope, tool, mode, handle's provider + external id, outcome, counts, latency). **Never** the token, never the result text. | "Who looked at what" must be answerable; storing results would be a second copy of tenant data. |
| D15 | **Three settings only:** `LIVE_TOOLS_ENABLED` (off), `LIVE_TOOLS_PROVIDERS`, `LIVE_TOOLS_ORGS`. Everything else is a module constant until `live_tool_calls` shows it needs tuning. **No result cache** until the audit shows repeat calls. | Configuration nobody tunes is configuration that drifts (CLAUDE.md §2). |
| D16 | **A failed live read falls back to the indexed answer — except when the provider says the object is gone or access was revoked.** Timeout (constant 6s), 401/reauth, rate limit (including a rate-limit 403), 5xx ⇒ the indexed answer, with the chunk's sync time still visible. **404, "entity not found", or a 403 whose reason is permission ⇒ the stale chunk is withheld** (§5). Every outcome audited. | A live read is an enhancement, so a transient failure must not cost the answer; but a revoked or deleted object must not keep answering from the index. |
| D17 | **Later, not now:** GitHub onto the gateway, per-user tokens (hybrid, provider-native permissions), an MCP surface. Vendor MCP servers are never wired straight to the model (they want the raw token). | Each is additive once the gateway exists. |

---

## 2. What we add

```
app/livetools/
  __init__.py
  base.py         # LiveRead (text, fetched_at, truncated), refreshable-provider registry
  handles.py      # assign handles to a request's hits (fresh AND reused); resolve handle ->
                  #   (provider, external_id); strip_handles(answer) for the edge
  gateway.py      # refresh(hits, *, org_id, workspace_id, viewer, mode) -> list[LiveRead]
                  #   the ONLY code that decrypts a token for a live read
  linear.py       # Phase 1: issue by identifier (+ last N comments); maps GraphQL
                  #   "Entity not found" (HTTP 200) to not_accessible
  drive.py        # Phase 2: file by id (text export, size-capped)
  notion.py       # Phase 3: page by id (shared char budget)
  slack.py        # only after D10 clears: thread by (channel, ts)
  audit.py        # live_tool_calls writer; best-effort, never fails an answer
```

| Existing file | Change |
|---|---|
| `app/config/settings.py` | `LiveToolsSettings.from_env()` — the three settings (D15) |
| `app/db/schema.sql` | `live_tool_calls` (additive, `IF NOT EXISTS`, after its parents) |
| `app/rag/access_notice.py` | a second notice, "the matching <connector> item is no longer available (deleted, or access was removed)", chosen in `_gate_failed` when `live_withheld` — connector named, title never |
| `app/api/chat.py` | `deep_research` body flag → `LiveRequest` (D0); `live_withheld` keeps the refusal out of `feedback_and_gaps`, like `access_restricted`. `slack_events.py` is unchanged: Slack has no deep research, so it never sets a `LiveRequest` |
| `app/rag/context_assemble.py` | (Phase 2, with mode B) `describe_hit` renders the handle (`[L2]`) |
| `app/rag/retrieval.py` | `RetrievalResult.gate_document_id` — the document behind `gate_score`, set where the max is taken (D8a) |
| `app/rag/pipeline.py` | a withheld GATE document drops all its chunks and `gate_score` is recomputed from the remaining hits before the gate decision (any other withheld document leaves it untouched); below 0.35 ⇒ `_gate_failed` with the "no longer available" notice and `live_withheld=True` (D8a); mode A: refresh before generation, live block prepended to `contexts` (so the audit sees it); guard in enforce mode; `_is_cacheable` false when live used; handles minted on the `_try_reuse` path too; `strip_handles` beside the `MODE:` tag parse; mode B on the refusal path (Phase 2) |
| `app/api/chat.py` | `done.live_sources` (provider + fetched_at) |
| `frontend/components/ProvenanceStripe.tsx`, `app/chat/page.tsx`, `lib/sse.ts` | "Linear · live" chip; the **Deep research** composer toggle (D0) |
| `CLAUDE.md` | §3 decisions, §6 table, §7 state |

---

## 3. End-to-end flow (Phase 1)

Bo asks **"What's the latest on SYV-5?"**

1. Routing: exact graph seed `SYV-5` → Linear (`graph-named`) — unchanged.
2. Retrieval: the indexed SYV-5 chunk comes back, already filtered for Bo; its
   context line reads `[L1] SYV-5 - Build the activity scheduler · Linear · Sana · 28 Sep`.
3. Mode A: `L1` is a Linear issue → the gateway resolves `L1` to
   `(linear, SYV-5)` from the request's own hits, decrypts the Linear token, reads
   the issue (status, assignee, priority, last 5 comments), discards the token.
4. Checks (D9): guard (enforce mode), scrub, 6000-char cap; block labelled
   *"Live from Linear, fetched 12:04"*, placed ahead of the stale chunk inside
   `contexts`.
5. Generation with the unchanged strict prompt; the audit sees the live block;
   link rule treats it as shown text.
6. `done.live_sources=[{"provider":"linear","fetched_at":"…"}]`; not cached;
   audit row written.

The model saw handles and filtered text; never a token, URL, header, id, or any
object retrieval did not return for Bo.

---

## 4. Refreshable objects (not tools the model browses)

| Phase | Provider | Object (from a hit) | Live call | Scope | Per-person |
|---|---|---|---|---|---|
| 1 | Linear | issue (`SYV-5`) | GraphQL `issue(id)` + last N comments | token-wide (D7, stated) | inherited from retrieval (scope-level today) |
| 2 | Drive | file | `files.get` + text export, size cap | connected folder (index already enforced) | inherited — incl. owner-only (D8) |
| 3 | Notion | page | `pages.retrieve` + bounded block walk | pages shared with the integration | inherited (scope-level) |
| after D10 | Slack | thread | `conversations.replies` (≤15 msgs if restricted) | connected channels | inherited — private-channel ACL already on the row |
| — | GitHub | (already live) | existing `githublive` | authorized repos | existing |

Mode B (Phase 2) exposes the same reads as one tool, `refresh(handle)`, offered
only on a refusal/gate miss. There is no search tool (D2, D3).

---

## 5. Permissions & access management

1. **Company / space** — the token is resolved with `org_id` **and**
   `workspace_id` (`credentials.get_live_connection_token`); a space never falls
   back to the company connection.
2. **Target** — only handles minted for *this request's* hits resolve; anything
   else is refused and audited as `unknown_handle`. The model cannot name an
   arbitrary object.
3. **Person** — already decided by retrieval's `visibility_predicate` for that
   row (D3/D8). The gateway additionally refuses when the viewer cannot be built
   (no identity ⇒ no live read), and withholds the stale chunk when the live read
   says the object is gone or access was revoked — decided by the provider's
   *reason*, never the status code alone (Google returns 403 for rate limits;
   Linear returns HTTP 200 for a deleted issue).

| Situation | Behaviour |
|---|---|
| 404, or Linear's HTTP 200 with a GraphQL `Entity not found` error | stale chunk withheld; "not found or not accessible" (never "deleted"); not retried; outcome `not_accessible`; gate re-checked only if it was the gate document (D8a) — below 0.35 ⇒ `_gate_failed` with the notice |
| 403 whose provider reason is **permission** (Drive `forbidden` / `insufficientFilePermissions`, Notion `restricted_resource`, Slack `not_in_channel` / `channel_not_found`) | stale chunk withheld, as above |
| 403 whose reason is a **rate limit** (Drive `rateLimitExceeded` / `userRateLimitExceeded`), or a 429 / Slack `ratelimited` | **rate-limit row**: indexed answer, outcome `rate_limited` — a rate limit never hides a document |
| 403 with an unrecognised reason | treated as a rate limit/transient (indexed answer) and logged with the raw reason, so the mapping can be extended — withholding on a guess would hide documents on any new error code |
| 401, token expired and refresh fails | connection marked `needs_reauth` (existing); indexed answer; outcome `reauth` |
| Timeout / 5xx | indexed answer with its sync time; outcome `timeout` / `error` |
| Guard flags live text (enforce) | live block dropped, indexed answer; logged |
| No viewer identity | no live read at all |

---

## 6. Configuration

| Env var | Default | Meaning |
|---|---|---|
| `LIVE_TOOLS_ENABLED` | `false` | master switch |
| `LIVE_TOOLS_PROVIDERS` | `linear` | which providers may be refreshed |
| `LIVE_TOOLS_ORGS` | *(empty = all)* | org allow-list for staged rollout |

Constants in `app/livetools/base.py`: `MAX_REFRESHES = 2` per question,
`TIMEOUT_SECONDS = 6`, `MAX_CHARS = 6000`, `LINEAR_COMMENTS = 5`. No new
credentials, **no reconnect** for Phase 1 (Linear `read` scope suffices).

---

## 7. Data model

```sql
CREATE TABLE IF NOT EXISTS live_tool_calls (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id    UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    user_id         UUID REFERENCES users (id) ON DELETE SET NULL,
    conversation_id UUID,
    provider        TEXT NOT NULL,
    external_id     TEXT NOT NULL,          -- the refreshed object, from the documents row
    mode            TEXT NOT NULL,          -- 'refresh' | 'model'
    outcome         TEXT NOT NULL,          -- ok | not_accessible | unknown_handle | not_connected
                                            -- | timeout | reauth | rate_limited | guard_flagged | error
    truncated       BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms      INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_live_tool_calls_org_time ON live_tool_calls (org_id, created_at DESC);
```

90-day retention via the existing tick. No result text, no token.

---

## 8. Phases

### Phase 0 — groundwork and the Slack tier check (no user-visible change)
1. `LiveToolsSettings` (three settings, off).
2. `live_tool_calls` in `schema.sql`, verified on a throwaway DB.
3. `base.py`, `handles.py`, `gateway.py` skeleton, `audit.py`.
4. `LiveRequest` context + the `deep_research` flag on `POST /chat/stream` and a
   composer toggle; without it nothing below runs (D0). Handles (D4/D4a/D4b) move
   to Phase 2 with mode B.
5. `RetrievalResult.gate_document_id` set where `gate_score` is taken; a test pins
   that every candidate `.score` is a cosine (keyword leg included, see D8a).
6. **Slack tier check**: one real `conversations.history` call against staging,
   read the rate-limit response headers; if unclear, open a Slack support ticket.
   Record the result in this plan (D10).

**Exit:** suite green, flag off, zero behaviour change; Slack tier known.

### Phase 1 — Linear refresh, mode A only
1. `linear.py`: issue by identifier + last N comments. A GraphQL `errors` entry
   meaning "Entity not found" (Linear answers HTTP 200 for a deleted or
   inaccessible issue) maps to `not_accessible`; never read the status code alone.
2. Pipeline: refresh refreshable hits in parallel with prompt assembly; live
   block prepended to `contexts`; guard (enforce) + scrub + cap; `_is_cacheable`
   false; `done.live_sources`; pill "· live".
3. Tests (fakes that reject any unexpected URL — the Slack-source pattern):
   - the token never appears in the prompt, the live block, the audit row or the logs;
   - only this request's handles resolve;
   - **the answer audit receives the live block** (a fresh claim is not downgraded);
   - the link rule counts the live block as shown text;
   - enforce-mode guard drops a flagged live block;
   - Linear HTTP 200 + `Entity not found` ⇒ `not_accessible`, stale chunk withheld;
   - nothing withheld ⇒ `gate_score` and `gate_document_id` unchanged;
   - a withheld document that is NOT `gate_document_id` ⇒ `gate_score` unchanged,
     answer proceeds from the rest (even when the recompute over the final hits
     would have been lower — pins that we do not recompute);
   - the withheld document IS `gate_document_id` ⇒ ALL its chunks leave the hits and
     `gate_score` = best cosine of the rest; still ≥ 0.35 ⇒ answer from the rest;
     below ⇒ `_gate_failed`, "no longer available" naming Linear (not the issue
     title), `live_withheld=True`, not cached, no gap row;
   - every leg (vector, keyword, graph, fused) returns a cosine `.score`; a hit with
     `score=None` is skipped by the recompute;
   - a permission 403 withholds; a **rate-limit 403** (`rateLimitExceeded`) and a
     429 fall back to the indexed answer as `rate_limited`; an unknown 403 reason
     falls back and logs the reason; timeout/reauth ⇒ indexed answer; all audited;
   - "any update on it?" after a SYV-5 answer (reuse path) refreshes SYV-5;
   - ≤ `MAX_REFRESHES`; **zero extra model calls** in mode A.
4. Real-model e2e (the graph e2e harness): change SYV-5's status in Linear, ask
   without a sync ⇒ new status, labelled live; time-to-first-word vs. flag off.

**Exit:** tests green; fresh status without a sync; no token in any captured
prompt/log; median time-to-first-word regression ≤ 1s (measure the first token,
never the total).

### Phase 2 — Drive refresh + mode B on refusals
1. `drive.py` (`files.get` + export) by handle; owner-only rule inherited (D8).
2. Mode B: on a refusal/gate miss only, offer `refresh(handle)` in the same round
   as the web decision (no additional serial call where the web round already runs).
3. Tests as Phase 1, plus: a restricted Drive file never refreshed for a non-viewer
   (it never reaches the hits); mode B never offered on a grounded answer.

### Phase 3 — Notion refresh
`notion.py` with the shared char budget (existing Notion lesson).

### Slack — gated on D10
Only if the app is on the non-restricted tier (internal app or Marketplace-listed).
Then `slack.py`: thread by `(channel, ts)` resolved from the handle, ≤15 messages
if restricted. The ingestion impact of the restricted tier is a separate follow-up.

### Later
GitHub onto the gateway; per-user tokens (hybrid); MCP surface (only with a
concrete consumer).

---

## 9. Deployment & enabling

Inside the existing backend image — no new service.

| Environment | Backend (Render) | Frontend (Vercel) | Database |
|---|---|---|---|
| Staging | `handbook-staging` (deploys `staging`) | `hand-book-git-staging-hand-book.vercel.app` | staging Supabase |
| Production | `Hand-Book` (env in the dashboard, **not** `render.yaml`) | `hand-book.vercel.app` | prod Supabase |

The table is created on boot (`scripts/init_db.py` applies `schema.sql`).

1. `feat/live-tools` → `staging`; on `handbook-staging` set
   `LIVE_TOOLS_ENABLED=true`, `LIVE_TOOLS_PROVIDERS=linear`
   (`GRAPH_RETRIEVAL_ENABLED=true` stays on — mode A uses the plan).
2. Verify (§10); read `live_tool_calls`.
3. Merge to `main` with the flag **off** in prod; enable for one org via
   `LIVE_TOOLS_ORGS`, then all.

**Rollback:** `LIVE_TOOLS_ENABLED=false`; nothing to migrate back.

---

## 10. Verification checklist (staging)

- [ ] Change SYV-5's status in Linear; without a sync, "what's the status of
      SYV-5?" answers the new status, labelled live.
- [ ] A Linear comment containing an injection attempt is flagged (enforce) and
      the answer falls back to the index.
- [ ] Revoke the Linear token ⇒ indexed answer, reconnect shown, outcome `reauth`.
- [ ] Delete an issue in Linear ⇒ its stale chunk is withheld, "not found or not
      accessible" (Linear answers HTTP 200 + `Entity not found`, not 404).
- [ ] Ask about that deleted issue ⇒ "the matching Linear item is no longer
      available", not "I don't know"; no row in `feedback_and_gaps`.
- [ ] Ask about SYV-5, then "any update on it?" ⇒ the follow-up is refreshed live.
- [ ] No answer, cached answer, stored turn or Slack reply contains `[L1]`-style handles.
- [ ] `live_tool_calls` rows exist; no token and no result text in them.
- [ ] Render logs contain no token prefix.
- [ ] Time-to-first-word within budget.

---

## 11. Observability

`live_tool_calls` + logger `livetools` (outcome, latency, truncation — no text).
Refresh rate per question and repeat-object rate decide whether a cache or more
settings are ever needed (D15).

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Prompt injection in live text | guard (enforce), fence + scrub, read-only, results only to the asker (D9, D2) |
| Slack throttling | Slack gated on the tier check (D10) |
| Latency | mode A runs in parallel with prompt assembly, zero model calls; 6s timeout; degrade to index |
| Linear / Notion per-person gaps | stated (D7); inherited, never widened (D3) |
| Audit rejects fresh answers | live block inside `contexts` + a test (D9) |

---

## 13. Out of scope

Write actions; a generic HTTP tool; provider search tools; replacing the index;
charts from live data (numbers still come only from `activity_facts`).

---

## 14. Revision 2 — what changed and why

| Review point | Change |
|---|---|
| Slack restricts `conversations.history` / `.replies` to ~1/min, 15 msgs for non-Marketplace commercial apps | D10: Slack gated on a Phase 0 tier check; Phase 1 is Linear only; ingestion impact noted as a follow-up |
| Mode B asked for ids the model never sees (`slack_read_thread(channel_id, thread_ts)`) | D4: opaque handles on every hit, resolved server-side; D3: refresh only what retrieval returned |
| Mode B adds a serial model round to every question | D5/D6: Phase 1 is mode A (zero model calls); mode B only on refusal/gate miss and in deep research |
| Linear has no connected-team scope | D7: stated honestly as token-wide; team picker optional and separate |
| Live text must pass guard, audit and link rule, not only scrubbing | D9 + Phase 1 tests for each |
| Drive "drop unreadable" contradicted the index's owner-only fallback; `in parents` misses nested folders | D8/D11: one access model, refresh-by-handle only, no `drive_search` |
| 11 settings, a factory, a cache for Phase 1 | D12/D15: three settings, constants, no factory, no cache until the audit shows need |

### Revision 4

| Change | Why |
|---|---|
| D0: live reads only in deep research mode (`deep_research` flag, composer toggle) | normal search/Q&A keep the graph + index path unchanged; live is opt-in per question |
| Handles (D4/D4a/D4b) move to Phase 2 with mode B | mode A resolves targets server-side from the hits; a handle has no reader until the model can name one |

### Revision 3

| Review point | Change |
|---|---|
| D16 (403 ⇒ fallback) contradicted §5 (403 ⇒ withhold); Google uses 403 for rate limits | D16 rewritten; §5 decides by the provider's reason: 404 / permission-403 withhold, rate-limit-403 and 429 fall back, unknown 403 falls back and is logged |
| Linear returns HTTP 200 + `Entity not found` for a deleted issue | `linear.py` maps it to `not_accessible`; Phase 1 test + checklist item |
| The model may echo handles into answers | D4a: stripped at the edge beside the `MODE:` parse, before stream/cache/turn/Slack/gap log; test |
| `_try_reuse` answers from the previous turn's chunks | D4b: handles minted for reused hits; follow-up refresh test + checklist item |
| Restricted Slack tier caps every call at 15 messages | D10: ingestion is affected, not only live reads; sized by the Phase 0 tier check |
| A withheld chunk may be the only hit that passed the 0.35 gate | D8a: `live_withheld` refusal through `_gate_failed` with a "no longer available" notice (connector, never title), kept out of the cache and the gap log |
| The final hits cannot reproduce `gate_score` (a max over ~30 candidates before rerank) | D8a: `RetrievalResult.gate_document_id`; recompute only when the withheld document IS the gate document, dropping all its chunks — can only lower the score (safe direction) |
| Are all candidate scores cosines? | D8a: verified — both legs select `1 - (embedding <=> q)`, embedding `NOT NULL`, RRF keeps the chunk's score; `None` skipped anyway, pinned by a test |
| No gap row on a real deletion is debatable | D8a: stated as deliberate — a 404 / `Entity not found` cannot tell deletion from revocation, and a false gap costs more than a missed one |
