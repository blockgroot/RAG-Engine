# Plan: live connector access for the Second Brain (the live-tools gateway)

Status: **proposed, revision 2** · Branch: `feat/live-tools` · Date: 2026-09-29
Owner: Second Brain · Related: `docs/plans/2026-09-23-second-brain.md`, `app/githublive/`

Revision 2 folds in a review of revision 1 (§14 lists every change and why). The
short version: **the index finds, the live call refreshes** — for every
connector — and Phase 1 is **Linear only, with no extra model call**.

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
| D1 | **The model never holds a token.** Only the gateway calls provider APIs. | A token the model never had cannot leak through output or prompt injection. |
| D2 | **Read-only, fixed operations; no generic HTTP tool; no search tool.** | A generic tool reaches everything the token can; a provider search bypasses our visibility predicate. Read-only removes the "injected instruction makes the bot act" class. |
| D3 | **The index finds, the live call refreshes — for every connector.** A live read targets only a document retrieval returned for this asker. | Permissions are inherited from retrieval (which filters by `org_id`, `workspace_id`, scope and `visibility_predicate` before ranking) instead of re-implemented per provider. It also removes Slack's user-token search problem and Drive's nested-folder walk. |
| D4 | **Opaque handles, resolved server-side.** Every hit in the context carries a handle (`[L2]`, `[S3]`); anything that names a target passes the handle, and the gateway maps it to `(provider, external_id)` from the `documents` row of *this request's* hits. | The model never sees a channel id, thread ts or file id, so it cannot aim a call at something retrieval did not clear. Fixes the `list_reviews`-class bug (CLAUDE.md): a tool needing a value not in the context is unreachable. |
| D5 | **Phase 1 is mode A only: graph/retrieval-guided refresh, zero extra model calls.** When the graph plan or the top hits contain a refreshable object, the gateway re-reads it in parallel with prompt assembly, and the live block supersedes the stale chunk. | Handles the headline case ("latest on SYV-5") with no latency from a second model round and no cost against the 15 rpm budget. |
| D6 | **Mode B (the model requests a live read) is offered only on a refusal / gate miss — where the web tool already sits — and in deep research.** Never on every question. Arguments are handles only. | Grounded generation does not call tools today (`generate_with_tools` is used only by attachment paging, the web decision, GitHub and schedulers); offering tools on every answer means a serial tool-choosing round on every question. On a refusal that round is already being paid for. Settles rev 1's open question. |
| D7 | **Scope is honest per connector.** Slack = connected `channel_ids`; Drive = connected `folder_id`; GitHub = authorized repos; **Linear = everything the token can see** (no team scope exists in `source_config`, `autosync.SCOPE_KEYS` has only google/slack); **Notion = pages shared with the integration**. Because of D3 the live read can never exceed what the index already exposes; no guard is claimed that does not exist. A Linear team picker is a separate, optional change. | A claimed guard that does not exist is worse than a stated gap (CLAUDE.md, document-level access). |
| D8 | **One access model with the index.** Per-person visibility is exactly what `visibility_predicate` already decided at retrieval — including Drive's **owner-only fallback** for unreadable sharing, not a separate "drop" rule. Freshness adds one check: if the live read shows the object is gone or no longer accessible to the connection (404/403), the stale chunk is withheld too ("not found or not accessible"). | Two rules would show the same person a document in a normal answer and not in a live one. |
| D9 | **Live text passes every check indexed text passes:** fenced + scrubbed (`security/untrusted.py`); **injection guard** under `GUARD_MODE=enforce`, one batched call over the live blocks like web snippets (shadow mode logs); the live block is part of the **`contexts` handed to `_audit_answer`** (prepended the way `extra_contexts` is), so a correct fresh claim is not downgraded to a refusal; the block is in the prompt, so it counts as shown text for the **link rule** in `security/links.py`. | A Linear comment is exactly where a planted instruction sits; an audit that cannot see the live block would reject the very freshness this feature adds. |
| D10 | **Slack live reads wait on the rate-limit tier.** Since mid-2025, commercially distributed apps outside the Slack Marketplace get `conversations.history` / `conversations.replies` at ~1 request/min and 15 messages per request; internal apps keep ~50/min. Handbook installs into other workspaces via OAuth, so it is almost certainly on the restricted tier. Phase 0 checks the tier (response headers on a real call, or a Slack support ticket). **Restricted ⇒ no Slack live reads until a Marketplace listing**, and the ingestion impact (`slack.py` requests `limit: 200`) is its own follow-up. | Both Slack live methods are exactly the throttled ones; a live read that takes a minute is not live. |
| D11 | **Drive: refresh-by-handle only.** `drive_read_file` for an already-indexed, already-visible file (from its handle). No `drive_search`. | Nested folders would need a folder walk per question; D3 makes it unnecessary and keeps the owner-only rule (D8). |
| D12 | **The gateway lives in the backend, not a separate proxy service; no `factory.py`.** It is an orchestrator over existing providers, like `app/rag/` (CLAUDE.md §2's exception). | The protection is the checks, not the process boundary. A second Render service costs free-tier hours and a network hop. |
| D13 | **Live answers are never written to the scope-wide answer cache** (`_is_cacheable`). | Time-sensitive and assembled for one asker. |
| D14 | **Every live read is audited** in `live_tool_calls` (who, scope, tool, mode, handle's provider + external id, outcome, counts, latency). **Never** the token, never the result text. | "Who looked at what" must be answerable; storing results would be a second copy of tenant data. |
| D15 | **Three settings only:** `LIVE_TOOLS_ENABLED` (off), `LIVE_TOOLS_PROVIDERS`, `LIVE_TOOLS_ORGS`. Everything else is a module constant until `live_tool_calls` shows it needs tuning. **No result cache** until the audit shows repeat calls. | Configuration nobody tunes is configuration that drifts (CLAUDE.md §2). |
| D16 | **Failure never costs the answer.** Timeout (constant 6s), 401/403, reauth, rate limit ⇒ the indexed answer, with the chunk's sync time still visible; outcome audited. | A live read is an enhancement. |
| D17 | **Later, not now:** GitHub onto the gateway, per-user tokens (hybrid, provider-native permissions), an MCP surface. Vendor MCP servers are never wired straight to the model (they want the raw token). | Each is additive once the gateway exists. |

---

## 2. What we add

```
app/livetools/
  __init__.py
  base.py         # LiveRead (text, fetched_at, truncated), refreshable-provider registry
  handles.py      # assign handles to a request's hits; resolve handle -> (provider, external_id)
  gateway.py      # refresh(hits, *, org_id, workspace_id, viewer, mode) -> list[LiveRead]
                  #   the ONLY code that decrypts a token for a live read
  linear.py       # Phase 1: issue by identifier (+ last N comments)
  drive.py        # Phase 2: file by id (text export, size-capped)
  notion.py       # Phase 3: page by id (shared char budget)
  slack.py        # only after D10 clears: thread by (channel, ts)
  audit.py        # live_tool_calls writer; best-effort, never fails an answer
```

| Existing file | Change |
|---|---|
| `app/config/settings.py` | `LiveToolsSettings.from_env()` — the three settings (D15) |
| `app/db/schema.sql` | `live_tool_calls` (additive, `IF NOT EXISTS`, after its parents) |
| `app/rag/context_assemble.py` | `describe_hit` renders the handle (`[L2]`) |
| `app/rag/pipeline.py` | mode A: refresh before generation, live block prepended to `contexts` (so the audit sees it); guard in enforce mode; `_is_cacheable` false when live used; mode B on the refusal path (Phase 2) |
| `app/api/chat.py` | `done.live_sources` (provider + fetched_at) |
| `frontend/components/ProvenanceStripe.tsx` | "Linear · live" chip |
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
   says the object is gone or inaccessible.

| Situation | Behaviour |
|---|---|
| Provider 404 / 403 | stale chunk withheld; "not found or not accessible" (never "deleted"); not retried |
| Token expired, refresh fails | connection marked `needs_reauth` (existing); indexed answer |
| Timeout / rate limit | indexed answer with its sync time; outcome audited |
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
4. `describe_hit` renders handles; tests pin that handles resolve only within the
   request and that no raw id/ts/file id reaches the prompt.
5. **Slack tier check**: one real `conversations.history` call against staging,
   read the rate-limit response headers; if unclear, open a Slack support ticket.
   Record the result in this plan (D10).

**Exit:** suite green, flag off, zero behaviour change; Slack tier known.

### Phase 1 — Linear refresh, mode A only
1. `linear.py`: issue by identifier + last N comments.
2. Pipeline: refresh refreshable hits in parallel with prompt assembly; live
   block prepended to `contexts`; guard (enforce) + scrub + cap; `_is_cacheable`
   false; `done.live_sources`; pill "· live".
3. Tests (fakes that reject any unexpected URL — the Slack-source pattern):
   - the token never appears in the prompt, the live block, the audit row or the logs;
   - only this request's handles resolve;
   - **the answer audit receives the live block** (a fresh claim is not downgraded);
   - the link rule counts the live block as shown text;
   - enforce-mode guard drops a flagged live block;
   - 404/403 withholds the stale chunk; timeout/reauth ⇒ indexed answer; audited;
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
      accessible".
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
