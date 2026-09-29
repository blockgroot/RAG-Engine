# Plan: live connector access for the Second Brain (the live-tools gateway)

Status: **proposed** · Branch: `feat/second-brain-foundation` · Date: 2026-09-29
Owner: Second Brain · Related: `docs/plans/2026-09-23-second-brain.md`, `app/githublive/`

---

## 0. Summary

Today every answer except GitHub's comes from **our index**, which is at most one
sync interval (1h) old and holds only what ingestion stored (no ticket comments,
no current status between syncs). This plan lets the model use the connectors'
**live APIs** at question time, under three requirements that are all
non-negotiable:

1. **Live** — data is fetched from the provider when the question is asked.
2. **No token exposure** — the model never sees, receives, or can obtain a
   provider token, URL, or header.
3. **Permissions** — every result is limited to (a) the company/space, (b) the
   connection's scope, and (c) what *this asker* may see, before the model sees it.

The mechanism is a **gateway** inside the backend (`app/livetools/`). The model can
only *request* an action from a fixed, read-only list; the gateway validates the
request, injects the scope, calls the provider with the stored token, filters the
result for the asker, cleans it, audits it, and returns only the filtered text.
GitHub already works this way (`GitHubAgent`); this generalises it to every
connector.

```
Model ──"linear_get_issue(id=SYV-5)"──▶  GATEWAY  (our server, app/livetools/gateway.py)
                                           1. tool on the allow-list?        else refuse
                                           2. args valid + within bounds?    else refuse
                                           3. connector connected in scope?  else "not connected"
                                           4. inject connection scope (folder / channels / teams / repos)
                                           5. decrypt token → call provider → discard token
                                           6. filter rows for THIS asker (fail closed)
                                           7. scrub untrusted text, cap size, mark truncation
                                           8. write audit row
Model ◀──── filtered, labelled text only ──┘
```

---

## 1. Decisions (what we decided and why)

| # | Decision | Why |
|---|---|---|
| D1 | **The model never holds a token.** It emits a tool name + arguments; only the gateway calls APIs. | Anything in a prompt can be leaked by the model or extracted by prompt injection. A token the model never had cannot leak. |
| D2 | **A fixed, read-only allow-list of tools per connector. No generic "HTTP request" / "call endpoint" tool.** | A generic tool gives the model everything the token can reach (every channel, every file, write endpoints). The tool list IS the set of answerable questions — the rule `tests/test_github_tools.py` already enforces for GitHub. |
| D3 | **Read-only. No write tools at all** (no post, comment, create, update). | Removes the whole class of "injected instruction makes the bot act" risks. Revisit only with an explicit per-action user confirmation design. |
| D4 | **Scope is injected by the server, never taken from the model.** Drive = connected folder, Slack = connected channels, GitHub = authorized repos, Linear = connected teams, Notion = pages shared with the integration. Model args can narrow, never widen. | The connection's scope is an admin decision; a model argument must not override it. |
| D5 | **Per-person filtering reuses the existing access model** (`Viewer`, `security/visibility.py` semantics: emails, `domain:`, `group:`, `channel:`). | One definition of "who may see this" for index and live alike; a second spelling would drift (the reason `test_visibility.py` exists). |
| D6 | **Fail closed.** If a row's permissions cannot be read, the row is dropped (Drive), or the asker must be a verified member (Slack private channel). A permission-check error drops the result, never passes it. | A partial answer is recoverable; a leaked document is not. |
| D7 | **Coverage is honest and partial**, exactly as for the index: Drive and Slack are per-person; **Notion stays scope-level** (no per-page permission API); **Linear stays scope-level** until team membership is wired. The UI says which. | A half-enforced guarantee that reads as whole is worse than none (CLAUDE.md, document-level access). |
| D8 | **The gateway lives inside the existing backend, not as a separate proxy service.** | The protection comes from the checks, not the process boundary. A second Render service costs free-tier instance-hours (750h/month is ~one always-on service), adds a network hop and needs its own auth. |
| D9 | **Expose the same gateway as an MCP server later, if an external agent needs it** — still holding the tokens. Do NOT wire vendors' own MCP servers straight to the model. | Vendor MCP servers expect the raw token and expose broad operations with no per-user filter. |
| D10 | **The index stays primary; live calls add freshness and un-indexed data.** Typical live uses: current status of SYV-5, a ticket's comments, the latest replies in a thread, GitHub (never indexed). | The index is fast and already filtered; live calls cost latency and provider quota. |
| D11 | **"Index finds, live refreshes" for Slack.** Slack's `search.messages` needs a *user* token (`search:read`); our connection is a bot token. So discovery comes from our index/graph (thread id), and the live call re-reads that exact thread (`conversations.replies`) and recent channel history (`conversations.history`) within the connected channels. | Works with the scopes every tenant already granted — **no reconnect**. |
| D12 | **Two ways live data enters an answer:** **(A) graph-guided refresh** — no extra model call: when the graph plan or the top hits point at a Linear issue / Slack thread, the gateway fetches the live version in parallel and it supersedes the stale chunk; **(B) model-requested tool** — the routed agent's generation is offered the live tools of the tools it may read (routed + connected set) through the existing tool-calling path (`generate_with_tools`, as `web_search` already is), **one round, never a loop.** | (A) is cheap and deterministic; (B) answers what retrieval can't anticipate ("latest comment on…"). One round bounds latency and cost. |
| D13 | **Live results are untrusted content**: fenced + scrubbed with `security/untrusted.py`, labelled "Live from Linear (fetched 12:04)", size-capped with truncation stated. | A ticket comment can contain injected instructions; a partial result that looks complete is the failure CLAUDE.md §2 warns about. |
| D14 | **Answers that used live data are never written to the scope-wide answer cache** (`_is_cacheable`). | They were filtered for one person and are time-sensitive. |
| D15 | **Every call is audited** in `live_tool_calls`: who, org/space, tool, arguments (sanitised), result count, dropped-by-permission count, latency, outcome. **Never** the token, never the result text. | Needed to answer "who looked at what" and to tune limits; storing results would create a second copy of tenant data. |
| D16 | **Off by default** (`LIVE_TOOLS_ENABLED=false`), per-connector allow-list, optional per-org allow-list for staged rollout. | Same posture as `GRAPH_RETRIEVAL_ENABLED` and `GOOGLE_FORMS_ENABLED`: prove it on staging first. |
| D17 | **GitHub moves onto the gateway** (Phase 2) instead of staying a separate path. | One place for scope, audit, rate limits and caps. Behaviour of `GitHubAgent` is unchanged. |
| D18 | **Per-user tokens are a later, additive mode (hybrid):** if the asker linked their own account, use *their* token and the provider enforces their permissions natively; otherwise the company token + our filter. | Strongest enforcement, but every user must connect each tool — so it cannot be the only path. "Linked accounts" already does this for GitHub. |
| D19 | **Latency budget:** gateway calls run in parallel with retrieval where possible; per-call timeout 6s; a timed-out or failed live call degrades to the indexed answer with a note, never fails the question. | A live call is an enhancement; the index answer must always still be possible. |

---

## 2. What we add (components)

```
app/livetools/
  __init__.py
  base.py          # LiveTool contract, LiveResult (rows, dropped, truncated, fetched_at)
  catalog.py       # the allow-list: tool name -> provider, JSON schema, arg bounds, phase
  gateway.py       # run_tool(tool, args, *, org_id, workspace_id, viewer) -> LiveResult
                   #   the ONLY code that decrypts a token for a live call
  scope.py         # per-provider scope injection from oauth_connections.source_config
  permissions.py   # per-provider per-person filters (reuses Viewer)
  linear.py        # Linear GraphQL reads
  slack.py         # Slack Web API reads (bot token)
  drive.py         # Drive v3 reads (Phase 2)
  notion.py        # Notion reads (Phase 3)
  audit.py         # live_tool_calls writer (best-effort, never fails the answer)
  factory.py       # build_gateway()
```

Touched existing code:

| File | Change |
|---|---|
| `app/config/settings.py` | `LiveToolsSettings.from_env()` (§6) |
| `app/db/schema.sql` | `live_tool_calls` table (additive, `IF NOT EXISTS`) |
| `app/rag/pipeline.py` | (A) graph-guided refresh hook before generation; (B) offer live tools in the generation tool list; `_is_cacheable` = false when live data used |
| `app/rag/prompts.py` | tool descriptions; a context-block label for live results |
| `app/agent/github_agent.py` | Phase 2: call through the gateway |
| `app/api/chat.py` | `done` event gains `live_sources` (provider + fetched_at) |
| `frontend/components/ProvenanceStripe.tsx` | "Linear · live" chip |
| `CLAUDE.md` | §3 decisions, §6 table, §7 state |

---

## 3. End-to-end request flow

Bo asks **"What's the latest on SYV-5?"**

1. **Routing** (unchanged): graph plan sees exact seed `SYV-5` → Linear (`graph-named`).
2. **Retrieval** (unchanged): indexed SYV-5 chunk found (status as of last sync).
3. **(A) Graph-guided refresh:** the plan names a Linear issue → gateway
   `linear_get_issue(SYV-5)` runs in parallel with generation prep → live status,
   assignee, priority, last 5 comments. The block is labelled *"Live from Linear,
   fetched 12:04"* and placed ahead of the stale chunk.
4. **(B) If the model still needs more** (e.g. "what did Sana say in the thread
   about it?"), it may request one tool, e.g. `slack_read_thread(...)` for a
   thread the index/graph identified. The gateway runs steps 1–8 of §0.
5. **Generation** with the strict prompt; the same grounding rules and the same
   0.35 gate on the indexed part.
6. **Response:** `done.live_sources=[{"provider":"linear","fetched_at":"…"}]`,
   pill shows *Linear · live*; not cached; audit rows written.

What the model saw: tool names/descriptions, and filtered text. What it never saw:
the Linear token, the API URL, any header, any row Bo cannot access.

---

## 4. Tool catalog (the allow-list)

Every tool: read-only, typed args, max results, timeout, response char cap.

| Phase | Tool | Provider call | Scope injected by server | Per-person filter |
|---|---|---|---|---|
| 1 | `linear_get_issue(id)` | GraphQL `issue(id)` + comments (last N) | issue's team ∈ connected teams (else "not found") | scope-level (D7) |
| 1 | `linear_list_issues(assignee?, state?, team?, updated_since?)` | GraphQL `issues(filter)` | team filter forced to connected teams; `first` ≤ 20 | scope-level (D7) |
| 1 | `slack_read_thread(channel_id, thread_ts)` | `conversations.replies` | channel ∈ connected `channel_ids` (else refuse) | private channel ⇒ asker's email ∈ `conversations.members` (cached), or reply is in that channel (`channel:` rule) |
| 1 | `slack_recent_messages(channel_id, since?)` | `conversations.history` (limit ≤ 50) | same | same |
| 2 | `drive_search(query)` | `files.list` with `q` AND `'<folder>' in parents` + `permissions(...)` fields | connected folder (walk bounded as today) | file's `permissions` vs `Viewer.acl()`; unreadable ⇒ dropped |
| 2 | `drive_read_file(file_id)` | `files.get` + export text | file must be inside connected folder | same; plus size cap |
| 2 | GitHub's 6 tools | existing `app/githublive/` | authorized repos (existing) | installation-level (existing) |
| 3 | `notion_read_page(page_id)` | `pages.retrieve` + bounded block walk (shared char budget) | only pages shared with the integration | scope-level (D7) |

Rejected on purpose: generic HTTP tool (D2); any write (D3); Slack
`search.messages` (needs a user token — D11); cross-connector "search everything"
(that is the blended corpus the per-tool agents exist to avoid).

---

## 5. Permissions & access management (detail)

Three layers, all enforced in the gateway:

1. **Company / space** — the connection is resolved with `org_id` **and**
   `workspace_id` (`credentials.get_live_connection_token`), never falling back
   from a space to the company connection. A space member only gets tools for
   connectors connected in that space.
2. **Connection scope** — `scope.py` reads `oauth_connections.source_config`
   (folder_id, channel_ids, authorized repos, teams) and *adds* it to the provider
   query, or rejects a target outside it (e.g. `slack_read_thread` on a channel not
   in `channel_ids` → refused, audited as `out_of_scope`).
3. **Person** — `permissions.py` filters rows with the asker's `Viewer`:
   - `public_only` viewer (Slack channel reply) ⇒ only scope-public rows, plus the
     one `channel:<id>` being replied in — same rule as the index.
   - unrestricted viewer is **never** used for live tools (no identity ⇒ no live).
   - Result reports `dropped_by_permission` so the audit and the answer can say
     "some results were not shared with you" (never *which*).

Fail-closed matrix:

| Situation | Behaviour |
|---|---|
| Drive file with unreadable `permissions` | dropped |
| Slack membership lookup fails | private-channel rows dropped |
| Token expired and refresh fails | connection marked `needs_reauth` (existing), tool returns "reconnect needed", answer falls back to index |
| Provider 404 / 403 | "not found or not accessible" (never "deleted"), not retried |
| Viewer cannot be built | no live tools offered at all |

---

## 6. Configuration (all in `app/config/settings.py`, frozen dataclass + `from_env`)

| Env var | Default | Meaning |
|---|---|---|
| `LIVE_TOOLS_ENABLED` | `false` | master switch |
| `LIVE_TOOLS_PROVIDERS` | `linear,slack` | which connectors' tools may be offered |
| `LIVE_TOOLS_ORGS` | *(empty = all)* | optional org-id allow-list for staged rollout |
| `LIVE_TOOLS_REFRESH_ENABLED` | `true` | mode (A) graph-guided refresh |
| `LIVE_TOOLS_MODEL_CALLS` | `true` | mode (B) model-requested tools |
| `LIVE_TOOLS_MAX_CALLS` | `2` | max gateway calls per question (A + B combined) |
| `LIVE_TOOLS_TIMEOUT_SECONDS` | `6` | per provider call |
| `LIVE_TOOLS_MAX_RESULTS` | `10` | rows per call |
| `LIVE_TOOLS_MAX_CHARS` | `6000` | text returned to the model per call |
| `LIVE_TOOLS_RATE_PER_MINUTE` | `30` | per org per provider (reuses `api_rate_counters`) |
| `LIVE_TOOLS_CACHE_SECONDS` | `60` | per-(org, viewer, tool, args) result cache; never shared across viewers |

No new provider credentials. **No reconnect needed** for Phase 1: Linear `read` and
the existing Slack bot scopes (`channels:history`, `groups:history`,
`channels:read`, `groups:read`, `users:read.email`) cover every Phase 1 tool.
Drive (Phase 2) uses the existing Drive read scope.

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
    tool            TEXT NOT NULL,
    mode            TEXT NOT NULL,          -- 'refresh' | 'model'
    args            JSONB NOT NULL,         -- sanitised, bounded; never a token
    outcome         TEXT NOT NULL,          -- ok | refused | out_of_scope | not_connected
                                            -- | timeout | error | reauth | rate_limited
    rows_returned   INT NOT NULL DEFAULT 0,
    rows_dropped    INT NOT NULL DEFAULT 0, -- removed by the per-person filter
    truncated       BOOLEAN NOT NULL DEFAULT FALSE,
    latency_ms      INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_live_tool_calls_org_time ON live_tool_calls (org_id, created_at DESC);
```

Retention: 90 days, swept by the existing tick. No result text is stored (D15).

---

## 8. Phases, steps and exit criteria

### Phase 0 — groundwork (no user-visible change)
1. `LiveToolsSettings` + flags (off).
2. `live_tool_calls` table in `schema.sql` (after its parents; verified against a
   throwaway DB — CLAUDE.md §5 "ALTER after CREATE").
3. `app/livetools/base.py`, `catalog.py`, `gateway.py` skeleton, `audit.py`, `scope.py`.
4. Tests: catalog has no write tool and no generic HTTP tool; gateway refuses an
   unknown tool / bad args / out-of-scope target; audit row written on every path.

**Exit:** suite green, flag off, zero behaviour change.

### Phase 1 — Linear + Slack, both modes
1. `linear.py`: `get_issue`, `list_issues` with forced team filter.
2. `slack.py`: `read_thread`, `recent_messages`; channel ∈ `channel_ids` check;
   private-channel membership filter (cached per channel, like ingest).
3. `permissions.py` for Slack; Linear marked scope-level.
4. Pipeline (A): graph plan / top hits with a Linear issue or Slack thread ⇒
   parallel refresh; live block supersedes stale chunk; labelled with fetch time.
5. Pipeline (B): offer live tools of the answer's tool set through
   `generate_with_tools`, one round; results fenced + scrubbed.
6. `_is_cacheable` false when live used; `done.live_sources`; pill "· live".
7. Tests (fakes that **reject any unexpected URL**, the Slack-source pattern):
   - token never appears in prompt, tool result, audit row or logs (assert on all four);
   - out-of-scope channel/team refused; private channel filtered for a non-member,
     visible to a member; `public_only` viewer sees public + the replying channel only;
   - timeout/403/expired token ⇒ indexed answer still returned, outcome audited;
   - at most `LIVE_TOOLS_MAX_CALLS` calls per question; one model round, never a loop.
8. End-to-end with the real model (the e2e harness used for the graph): "latest
   on SYV-5" after changing its status *without* a sync; "what did Sana reply in
   the scheduler thread"; a private-channel thread asked by a non-member;
   time-to-first-word graph-on vs live-on.

**Exit:** all tests green; e2e shows fresh status without a sync; no token in any
captured prompt/log; time-to-first-word regression ≤ 1.5s median on refresh.

### Phase 2 — Drive + GitHub onto the gateway
1. `drive.py` (`drive_search`, `drive_read_file`) with folder injection and per-file
   `permissions` filter (unreadable ⇒ dropped).
2. `GitHubAgent` calls through the gateway (behaviour unchanged, now audited and
   rate-limited).
3. Tests: restricted file hidden from non-viewer, visible to viewer; file outside
   folder refused; GitHub tool-surface test still passes.

**Exit:** as Phase 1, plus the Drive permission matrix proven on a live folder in staging.

### Phase 3 — Notion + per-user tokens (hybrid)
1. `notion.py` with the shared char budget (existing Notion lesson).
2. Per-user tokens: if the asker has linked their own account for a provider, the
   gateway uses it (provider-native permissions); else company token + filter.
   Starts with GitHub (already linkable), then Slack/Google/Linear user OAuth.

**Exit:** a linked user's live read returns only what their own account can see.

### Phase 4 (optional) — MCP exposure
Expose the gateway's tool list as an MCP server for external agents, authenticated
per org/user, same checks, same audit. Only if a concrete consumer exists.

---

## 9. Deployment & enabling

**Where it runs:** inside the existing backend image — no new service.

| Environment | Backend (Render) | Frontend (Vercel) | Database |
|---|---|---|---|
| Staging | `handbook-staging` (deploys the `staging` branch) | `hand-book-git-staging-hand-book.vercel.app` | staging Supabase |
| Production | `Hand-Book` (created by hand; env in the dashboard — **not** `render.yaml`) | `hand-book.vercel.app` | prod Supabase |

**Schema:** additive; `scripts/init_db.py` applies `schema.sql` on every boot
(`IF NOT EXISTS`), so deploying creates `live_tool_calls` automatically.

**Rollout steps**
1. Merge the phase into `feat/second-brain-foundation` → merge into `staging`
   (Render redeploys `handbook-staging`; Vercel builds the staging frontend).
2. On `handbook-staging` set `LIVE_TOOLS_ENABLED=true`,
   `LIVE_TOOLS_PROVIDERS=linear,slack`, optionally `LIVE_TOOLS_ORGS=<staging org id>`.
   `GRAPH_RETRIEVAL_ENABLED=true` stays on (mode A uses the plan).
3. Verify on staging (checklist §10), read `live_tool_calls`.
4. Merge to `main` → `Hand-Book` deploys **with the flag still off** (code dark in prod).
5. Enable in prod for one org via `LIVE_TOOLS_ORGS`, then all orgs.

**Rollback:** set `LIVE_TOOLS_ENABLED=false` on the service → effective on the next
request; no data migration to undo (the audit table can stay).

---

## 10. Verification checklist (staging, before prod)

- [ ] Change SYV-5's status in Linear; without waiting for a sync, "what's the
      status of SYV-5?" returns the new status with a *live* label.
- [ ] "What were the last replies in the scheduler thread?" returns replies posted
      after the last sync.
- [ ] A private-channel thread asked by a non-member: nothing from it appears;
      asked by a member: it does.
- [ ] Revoke / expire the Linear token: answer falls back to the index, card shows
      reconnect, audit outcome `reauth`.
- [ ] `live_tool_calls` rows exist for each call; no token or result text in them.
- [ ] Search Render logs for the token prefix: zero hits.
- [ ] Time-to-first-word within budget (measure first token, never the total — the
      typing effect is 50ms/word).

---

## 11. Observability

- `live_tool_calls` table (per call) and logger `livetools` (outcome, latency,
  rows, dropped, truncated — no text).
- Admin view later: calls per provider per day, refusal/timeout rates.
- Rate-limit hits surface as `rate_limited` outcomes, not errors.

---

## 12. Risks & open questions

| Risk | Mitigation |
|---|---|
| Prompt injection inside live content | Fenced + scrubbed (D13); read-only tools (D3); results only go to the asker. |
| Provider rate limits (Slack tier 3, Linear complexity) | Per-org limiter, 60s per-viewer cache, max 2 calls/question. |
| Latency | Refresh runs in parallel; 6s timeout; degrade to index. Budget measured on first token. |
| Linear & Notion per-person gaps | Stated in the UI (D7); Linear team membership is the next enforcement to wire. |
| Model picks a tool badly | Validation is the gate; a bad pick costs a refusal or an indexed answer, never another tenant's data. |
| Token refresh races (Google ~1h tokens) | Reuse `get_live_connection_token`, the one owner of refresh. |

Open: whether mode (B) should be offered on every question or only when the index
answer is stale/refused (default proposal: offered when the routed tool has live
tools enabled; measure call rate on staging before widening).

---

## 13. Out of scope

- Write actions of any kind.
- A generic API/HTTP tool.
- Replacing the index with live reads.
- Slack full-text live search (needs user tokens — revisit with Phase 3).
- Charts from live data (numbers still come only from `activity_facts`).
