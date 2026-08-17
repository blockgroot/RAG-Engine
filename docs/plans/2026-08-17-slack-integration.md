# Slack Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Implementation status (2026-08-17)

Built, phase by phase, each committed separately on `feature/slack-integration-research`:

- **Phase 1** (`SlackSettings`, `SlackOAuthProvider`, factory wiring) — done.
  Generic `/{provider}/authorize|callback` routes needed zero changes.
- **Phase 2** (`SlackAdapter` — thread-as-document, all four volume bounds
  from §6) — done.
- **Phase 3** (channel-picker admin endpoints, D10's naming groundwork,
  D11's contextual-retrieval-off-by-default gate) — done.
- **Phase 4/5** (workspace-scoped connect + picker, D10 cross-connection
  conflict guard, frontend `ConnectionCard`/`SlackChannelPicker` UI) — done.
- **Not implemented — D9's identity-grant step.** The plan's §9 called for a
  "Sign in with Slack" OIDC grant so a workspace's channel picker only shows
  channels the CONNECTING PERSON belongs to, not every channel the shared org
  bot can see. That was deferred: it's a materially different OAuth flow (a
  second, narrower scope grant with no existing pattern in this codebase to
  mirror) rather than an incremental extension of what shipped. What DID ship
  in its place is D10 (cross-connection dedupe — a channel can't be claimed
  by two connections at once), which is a different, narrower guarantee: it
  stops the same channel being double-claimed, but does not stop a workspace
  owner from picking a channel they personally aren't in, as long as the
  shared bot is already a member. Documented in-code on
  `GET .../slack-channels` (workspace route) and here for visibility.
- Test coverage: `tests/test_slack_oauth.py`, `test_slack_source.py`,
  `test_slack_utils.py`, `test_slack_contextual_gate.py`,
  `test_api_slack_admin.py`, `test_api_slack_workspace.py` — all passing
  against a real Postgres; existing `test_api_admin.py`/`test_api_workspaces.py`/
  `test_isolation.py` re-run clean (no regressions). Frontend `tsc --noEmit` clean.

**Goal:** Let an org admin connect specific Slack channels once, **and** let an
individual employee connect their own channels into a personal
Workspace-within-a-Workspace (private spaces + colleagues invited, exactly
like Notion/Drive/GitHub already support) — both answered from the existing
RAG pipeline: grounded, gated, cited.

**Revision note (2026-08-17, after design review):** the first draft of this
plan deferred workspace-scoped Slack as a v1 non-goal. That's reversed below
— §9 designs it properly, because parity with Notion/Drive/GitHub's
workspace-level connect was an explicit requirement, not a nice-to-have. Also
added: §7 (the actual onboarding UX, which wasn't concretely specified before)
and §8 (volume/backpressure controls for large channels — raised as a real
risk: a busy channel's history is not safe to embed unbounded).

**Architecture:** Slack is a **fourth** `SourceAdapter`, not a fourth agent.
Unlike GitHub (code, not prose — answered live, nothing embedded), Slack
messages *are* prose that benefits from semantic retrieval, and Slack's own
API rate limits make query-time live search a poor fit (see D1). So Slack
slots into the **existing ingest → chunk → embed → store → retrieve → gate →
generate** path, unchanged, via `app/sources/slack.py`. **No new agent.**
`PolicyAgent`/`WorkspaceAgent` answer Slack questions exactly as they answer
Notion/Drive questions today — the same gate, the same strict prompt, the
same citations. The org/workspace admin explicitly picks which channels to
connect (never "all channels" implicitly); one Slack thread = one ingested
document, mirroring how Onyx's community connector chunks (research below).

---



## 0. Research: how Glean and Onyx do this

Full agent research transcript is in this plan's history; summary that drove
the decisions below:

- **Onyx (open source)** ships two connectors. The default **indexed**
connector polls `conversations.history`/`conversations.replies` (no
webhooks, no Socket Mode) and groups an entire **thread into one document**
(`{channel_id}__{thread_ts}`), with a checkpoint per channel so a poll can
resume mid-history instead of re-walking from scratch. It auto-joins public
channels via `conversations.join` but **cannot** auto-join private channels
— Slack has no API for that; a human must `/invite` the bot. Per-user
ACL-aware retrieval ("Auto Sync Permissions") is **Enterprise-only**;
community builds index org-wide once a channel is connected — same
granularity our own Notion/Drive connectors already have. Onyx also ships a
newer **Slack Federated** connector that does live per-user `search.messages`
calls instead of indexing, but Onyx's own docs say it's lower-relevance and
recommend indexing when possible.
- **Glean** now leans on a "Real-Time Search" connector: message bodies are
fetched live at query time and **never written to Glean's index** (only
metadata — users, channels, membership — is persisted, refreshed via
Events-API webhooks). Glean's classic indexing connector still exists but is
now secondary. Glean's headline feature is **permission-aware search**: each
user does their own OAuth grant so a private message is only searchable by
someone who could already see it in Slack.
- **Slack platform mechanics that apply regardless of design**: a **bot
token** (`xoxb-`) sees only what it's been invited into; a **user token**
(`xoxp-`) sees only what that specific human can see — this is the
mechanism both Glean's and Onyx's per-user models exploit, and it requires
per-user OAuth, not a single org-wide install. Public channels: joinable
programmatically with `channels:history`. Private channels/DMs/group-DMs:
**no auto-join API exists**; explicit human invite is the only path, for
either bot or user tokens. `conversations.history` sits in one of Slack's
more restricted rate tiers, and Slack has been tightening it further for
non-Marketplace apps (bulk backfill against a busy workspace is genuinely
slow) — this is *the* reason Glean and Onyx's federated connector both moved
toward live query-time calls instead of full backfill.

**What we take from this, and what we don't:** Glean/Onyx's move to
"live/RTS" is chiefly solving **per-user permission-awareness** (a message a
manager can see that an IC can't) — a real gap in a *general-purpose*
enterprise search tool with thousands of channels. This project has no
per-user ACL layer anywhere yet (Notion pages, Drive docs, and GitHub repos
are all connected and readable org/workspace-wide once an admin connects
them) — adding one just for Slack would be a new access-control dimension
built speculatively, the same objection D-notes in the GitHub and Workspace
plans already raised and rejected elsewhere. So: **index, org/workspace-wide,
admin-selected channels only** — the same granularity as everything else,
not Glean/Onyx's per-user model. Revisit only if a customer explicitly needs
Slack-level DM/private-channel privacy inside the tool.

---



## 1. Decisions (with alternatives and why)


| #   | Decision                                                                                                                                                                                                                                                                      | Alternatives considered                                                                                                             | Why this                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | **Index Slack content** (SourceAdapter, like Notion/Drive) — **not** a live tool-calling agent like GitHub                                                                                                                                                                    | Live query-time fetch (Glean RTS / Onyx Federated style)                                                                            | Slack messages are prose our embedding/chunking/gate/prompt pipeline already handles well (unlike GitHub code). Live fetch's whole justification in Glean/Onyx is per-user permissioning we don't have (D-note above) — without that win, live fetch just inherits Slack's worse rate limits and worse relevance (Onyx's own docs concede this) for no benefit. Indexing also lets Slack answers get citations, hybrid search, and reranking for free — a live tool-call agent (GitHub) gets none of that.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| D2  | Admin **explicitly picks channels** to connect, from a list; never "connect all channels"                                                                                                                                                                                     | Auto-discover and index every channel the bot can see                                                                               | Same reasoning as Google's folder picker and GitHub's `repository_selection`: connecting shouldn't silently expand scope to everything, and Slack workspaces routinely have channels (exec, HR-sensitive, random) nobody intended to make queryable. Bot auto-joining every public channel the moment it's installed would be a real privacy surprise.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| D3  | **One Slack thread = one document** (`source_external_id = "{channel_id}:{thread_ts}"`), matching Onyx's community connector                                                                                                                                                  | Per-message documents; fixed time-window documents                                                                                  | A lone message is usually meaningless out of context (a reply "yes, EOD Friday" needs its parent question); a whole channel-day is too coarse and would blur unrelated conversations into one chunk. Thread-as-document is the natural conversational unit and is proven by Onyx's implementation. A message with no thread (no replies) is simply a one-message "thread."                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| D4  | **Bounded, checkpointed polling** of `conversations.history`/`conversations.replies`, no Events API / Socket Mode in v1                                                                                                                                                       | Real-time Events API webhook ingestion                                                                                              | Real-time push adds a new inbound HTTP surface (or Socket Mode's persistent connection) for freshness this project doesn't need yet — Notion/Drive are already poll-on-demand ("Check for changes" / re-ingest), and Slack should feel the same, not be the one source with different staleness semantics. `conversations.history`'s tightened rate limits (per-app, per-channel) also mean a bounded backward walk is required regardless — same shape as the existing Notion fetch-size bound and GitHub diff cap (§4 of `CLAUDE.md`): bound the walk itself, track a resume checkpoint, don't try to pull everything in one call.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| D5  | Slack is a `provider = "slack"` value on the existing `documents.source_provider` / `oauth_connections` machinery — **no new tables, no new columns**                                                                                                                         | A dedicated `slack_channels` table                                                                                                  | `source_config` JSONB on `oauth_connections` already exists precisely for this ("a future Slack adapter will need its own shape (a repo name, a channel list)" — literally anticipated in the existing docstring, see the GitHub plan's D3). Provider-partitioned sync (`documents.source_provider`) already guarantees a Slack sync can never wipe Notion/Drive rows, with zero new code for that guarantee.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| D6  | **One bot token (**`xoxb-`**) per Slack *team*, via the standard "Add to Slack" v2 OAuth flow, installed once by an org admin** — reused underneath both org-wide and personal/workspace connections                                                                          | Per-user OAuth for every connection (Glean/Onyx-federated style); a manually pasted bot token; a separate bot install per workspace | Slack only has one installable "app" per team — there's no GitHub-App-style per-installation scoping to lean on. So the bot install happens **once**, admin-driven, matching every other connector's OAuth shape (`app/auth/` `OAuthProvider`, `MultiFernet`-encrypted). What actually varies per connection (org-wide vs. a specific employee's workspace) is **which channels are registered under which** `workspace_id` (D9), not a second bot credential. A pasted token is the style Google deliberately moved away from (`CLAUDE.md` D3, Google section).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| D7  | **No auto-join for private channels; the UI must show "not yet accessible — invite the bot in Slack first."** Public channels *may* be auto-joined via `conversations.join` once selected.                                                                                    | Silently skip inaccessible channels; ask for a broader initial scope                                                                | Slack enforces this server-side (no scope bypasses it — research §0/§3), so the honest UI move is to say so, not hide it. This mirrors GitHub's "the admin picks scope on GitHub's own screen" and Google's `files.get`-validated folder — every connector here is explicit about what it can't reach rather than silently reducing coverage.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| D8  | **No new agent.** `PolicyAgent`/`WorkspaceAgent` (unchanged `RagPipelineAgent`) answer Slack questions                                                                                                                                                                        | A `SlackAgent` with its own tab, like `GitHubAgent`                                                                                 | GitHub *needed* a new agent because nothing is embedded (no `RagPipeline` to adapt — see the GitHub plan's D8). Slack has the opposite shape: content **is** embedded, so it's just more chunks in the same corpus the existing agents already query. Answers surface with citations back to a Slack thread URL exactly like a Notion page citation today.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| D9  | **Workspace-scoped Slack connect requires the connecting employee's own lightweight Slack identity grant** (`openid`/`identity.basic`, "Sign in with Slack" — not a second bot install), used ONLY to filter the channel picker to channels *that person* actually belongs to | Let a workspace owner pick from every channel the org's bot can see; reuse the org-wide `oauth_connections` row with no extra check | Without this, any workspace owner could add the whole company's `#exec` channel into their "personal" space just because the shared bot happens to already be in it — silently turning workspace membership into an access boundary over channels the owner was never actually part of. This is the direct Slack analogue of GitHub's `prefer_user_account` / `_reject_org_installation_for_workspace` check (§4 of the GitHub plan) and Drive's per-owner OAuth folder pick — same principle, adapted to Slack having one shared bot instead of per-installation scoping. Full flow in §9.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| D10 | **A channel can be registered under exactly one** `workspace_id` **value at a time** (org-wide `NULL`, or one specific workspace) — enforced at connect-time, app-level                                                                                                       | Allow the same channel to be added to multiple workspaces/org-wide simultaneously                                                   | The DB's partial-unique-index pair (`idx_documents_org_provider_external_orgwide` / `..._workspace`) does **not** stop this — different `workspace_id` values are different index entries, so the same channel could otherwise be ingested twice under two owners, doubling embedding cost and creating "which workspace answered this" ambiguity for identical content. Not a security bug (each copy is still correctly `workspace_id`-scoped at retrieval, so no cross-workspace leak) — it's a redundancy/confusion bug, closed by checking `source_config.channel_ids` across every existing connection for this org before saving a new one.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| D11 | **Contextual retrieval (**`INGEST_CONTEXTUAL_ENABLED`**) defaults OFF for** `provider="slack"`**; a per-sync total-chunk cap (**`SLACK_MAX_CHUNKS_PER_SYNC`**) skips further ingestion once hit, not just per-document**                                                      | Leave contextualization on for Slack same as Notion/Drive; rely only on `INGEST_CONTEXTUAL_MAX_CHUNKS` (per-document, already 200)  | Two gaps the per-document cap doesn't cover. (1) Contextual retrieval costs one LLM call per chunk — fine for a Notion page (few chunks), but a Slack sync producing thousands of thread-documents means thousands of LLM calls against the same 15rpm free-endpoint ceiling `CLAUDE.md` §4 already documents as a hard wall — worse by an order of magnitude than any existing source, since Slack's document count scales with conversation volume, not page count. A thread's own channel name + timestamp already gives most of the "situating context" the LLM call would add, so the value-per-call is lower here too — skip it by default, not just by budget. (2) `INGEST_CONTEXTUAL_MAX_CHUNKS` bounds one document's chunk count, but nothing bounds the SUM across a whole sync — a channel with many mid-size threads can still produce an unbounded total. `SLACK_MAX_CHUNKS_PER_SYNC` (default e.g. 20,000) stops accepting new documents once the running total for that sync hits it, logged as a truncation notice — same "bound the aggregate, not just the unit" instinct as `SLACK_BACKFILL_DAYS` bounding history and `SLACK_MAX_THREAD_MESSAGES` bounding one thread. |




## Note on D5's naming

D5 above still holds (`provider = "slack"`, `source_config` JSONB, no new
tables) — it's unaffected by D9/D10, which only decide *who* may register
which channel under which `workspace_id`, not the storage shape.

## 2. What changes, concretely

- `app/sources/slack.py` — new `SlackAdapter` implementing the existing
`SourceAdapter` contract:
  - `list_documents()` — for each configured `channel_id`: paginated
  `conversations.history` (bounded, checkpointed per D4), grouping each
  root message + its `conversations.replies` into one thread-document.
  Returns `(external_id, last_modified)` pairs like every other adapter —
  `last_modified` is the thread's latest reply timestamp, so an active
  thread re-syncs as "updated," not "removed + re-added."
  - `fetch_document(external_id)` — renders a thread to text: each message as
  `[HH:MM] display_name: text`, resolving `user_id`→display name via a
  cached `users.info`/`users.list` lookup (mirrors Notion's per-block
  rendering). Slack message "blocks" (rich text) are flattened to plain
  text; attachments/files are represented by name only in v1 (no file
  content fetch — same "bound the ingest surface" discipline as the Notion
  block-recursion fix in `CLAUDE.md` §4).
  - `get_last_modified(external_id)` — cheap `conversations.replies` call for
  just that thread's latest timestamp (used by "Check for changes").
- `app/auth/slack_oauth.py` — `SlackOAuthProvider` (`OAuthProvider`
interface): `authorize_url` = Slack's `oauth/v2/authorize` (bot scopes:
`channels:history`, `channels:read`, `channels:join`, `groups:history`,
`groups:read`, `im:history`, `im:read`, `mpim:history`, `mpim:read`,
`users:read`), `exchange_code` = `oauth.v2.access`. No native `refresh` —
Slack bot tokens don't expire under the standard OAuth2 (non-token-rotation)
install, so `OAuthProvider.refresh` stays the default no-op, same as
Notion's internal-integration token.
- **Post-connect channel picker** (new, because unlike GitHub's own install
screen, Slack's OAuth grant screen doesn't let the admin pick channels):
`GET /admin/connections/{id}/slack/channels` calls `conversations.list`
(public + private channels the bot can already see) and returns each with
`is_member` so the UI can show "connected" vs. "invite the bot first" (D7).
`PUT /admin/connections/{id}/slack/channels` writes the selected
`channel_ids` into `source_config`, same shape as Google's folder-URL PUT.
- **Sync** — `ingest_source`/`detect_source_changes` already accept an
explicit `provider`; `provider="slack"` is simply a new value, no pipeline
change. The existing removal-fraction guard (`_sanitize_removals`) and the
first-sync-retry guard both apply unmodified.
- **Frontend** — Sources page gets a Slack `ConnectionCard` (the existing
provider-agnostic component — no new component, per how Google/GitHub
already slotted in) plus one new small channel-picker view for the D7 flow.
- **Nothing in** `app/rag/`**,** `app/agent/`**, the gate, or the prompts changes.**



## 3. Schema — no new tables

Confirmed against §5 of `CLAUDE.md`: `documents.source_provider` is already a
free-form partition key, `oauth_connections.source_config` is already JSONB
reserved for exactly this ("a channel list"). Slack needs zero migrations.

`source_config` shape for a Slack connection:

```json
{
  "team_id": "T0123ABC",
  "team_name": "Acme Corp",
  "channel_ids": ["C0123ABC", "C0456DEF"],
  "channel_names": {"C0123ABC": "#general", "C0456DEF": "#eng-handbook"}
}
```



## 4. Rate limits and the bounded-walk discipline (carries `CLAUDE.md` §4 forward)

`conversations.history`/`conversations.replies` are rate-limited per-app,
tightened further by Slack for non-Marketplace apps pulling non-recent
history. Concretely:

- Cap initial backfill per channel to a configurable window
(`SLACK_BACKFILL_DAYS`, default e.g. 90) — same "bound the thing itself"
instinct as `IngestSanitizeSettings.max_document_chars` and the GitHub diff
cap. A channel with years of history does **not** get pulled in full on
first connect; log a truncation marker exactly like the Notion/GitHub
precedents, never truncate silently.
- Track a per-channel checkpoint (oldest timestamp reached) so an interrupted
or incremental sync resumes rather than re-walking — Onyx's
`channel_completion_map` pattern, adapted to this project's existing
`ingestion_jobs` progress fields (`phase`/`processed_documents`) rather than
a new job-state shape.
- 429s honor `Retry-After` with backoff — same pattern already implemented
for GitHub's REST client and the LLM's `LLMRateLimitError`. Do not add a
third bespoke retry implementation; extract the existing backoff helper if
it isn't already shared.



## 5. Onboarding flow, end to end (org-wide connect)

This answers doubt #1 directly — concretely, what the admin sees:

```
1. Admin opens Sources → clicks "Connect Slack".
      → GET /auth/slack/authorize          (existing session-authed route)
      → create_state(org_id, "slack")      (existing single-use server-side state)
      → 302 to https://slack.com/oauth/v2/authorize?scope=<bot scopes>&client_id=...&state=<state>

2. Slack shows ITS OWN install screen: which Slack workspace, and a
   confirmation of the bot scopes requested (channels:history, im:read, …).
   >>> Slack does NOT let the installer pick channels on this screen — that
   >>> only happens after install, in step 4. This is the one respect in
   >>> which Slack's flow is less complete than GitHub's own install screen,
   >>> and why step 4 exists at all. <<<

3. Slack redirects back:
      → GET /auth/slack/callback?code=...&state=...
      a. consume_state(state, "slack") -> org_id                (single-use)
      b. exchange_code(code) -> POST oauth.v2.access -> bot token + team_id/team_name
      c. save_connection(org_id, "slack", token, source_config={team_id, team_name})
      d. redirect to the new channel-picker page (not just "Sources", since
         a Slack connection with zero channels selected is not yet useful —
         same reasoning as Google requiring a folder before first sync)

4. Channel picker page: GET /admin/connections/{id}/slack/channels
      -> conversations.list (public + private channels the bot can already see)
      -> each row shows: name, public/private badge, and one of:
           "Ready"            (bot is_member = true)
           "Invite the bot"   (bot is_member = false — with the exact
                               "/invite @YourAppName" text to paste into Slack)
      Admin ticks the channels they want indexed, clicks Save.
      -> PUT /admin/connections/{id}/slack/channels {channel_ids: [...]}
         writes into source_config.channel_ids (D5); for any newly-picked
         PUBLIC channel not yet joined, this call also fires conversations.join
         (D7) so the very next sync can read it immediately.
      -> Picking a private channel the bot hasn't been invited to yet is
         allowed (saved for later) but flagged "waiting on Slack invite" until
         a re-check of is_member flips true — mirrors how a Google folder can
         be configured before the first successful sync.

5. Admin clicks "Sync now" (or waits for the first automatic run) exactly like
   Notion/Drive today — same ingestion_jobs queue, same progress UI.
```

**Discoverability of the picker after the fact:** the channel picker is not
only a first-connect step — it's reachable any time from the Slack
`ConnectionCard` ("Manage channels"), so an admin can add/remove channels
later without redoing the OAuth grant, the same way Google's folder can be
re-pointed without reconnecting.

## 6. Volume and embedding backpressure (doubt #2)

A busy `#general` or `#eng` channel can easily have tens of thousands of
messages across thousands of threads — indexing that unbounded would be both
slow (embedding + contextualization cost scales with message count, not
channel count) and low-value (most Slack chatter is not the kind of durable
fact a policy/handbook page is). Four bounds apply together, each reusing an
existing mechanism rather than inventing a new one:

1. **Time-bounded backfill** (`SLACK_BACKFILL_DAYS`, default 90 — open
  question §10.1). First sync only walks back that far; older history is
   simply not indexed, logged once as a truncation notice (the Notion/GitHub
   truncation-marker precedent). Re-syncs only ever walk *forward* from the
   last checkpoint (D4), so this bound is paid once, not on every sync.
2. **Noise filtering before a thread even becomes a document.** Unlike a
  Notion page (already curated prose), most Slack messages are not
   retrieval-worthy: a bare `:+1:` reaction, a one-word "thanks", a lone
   message with no reply. `SlackAdapter.list_documents()` skips a thread
   entirely if, after stripping mentions/emoji-only reactions, its combined
   text is under `SLACK_MIN_THREAD_CHARS` (default ~40) — this is a volume
   control **and** a quality control together (embedding "thanks 🙏" as its
   own chunk would only ever be noise competing with a real answer in
   retrieval). Not a novel idea here — it's the same instinct as
   `INGEST_CONTEXTUAL_MAX_CHUNKS` skipping enrichment on outsized input, just
   applied at the filter-before-ingest end instead of the enrich-after end.
3. **Per-thread size cap.** An extremely long-running thread (hundreds of
  replies) is truncated to the most recent `SLACK_MAX_THREAD_MESSAGES`
   (default ~50) once rendered to text, with the same
   `"[... content truncated ...]"` marker style as the Notion block-budget
   fix — never a silent cut. This bounds a single document's size the same
   way `CHUNK_MAX_CHARS` already bounds a single chunk's (`CLAUDE.md` §4);
   the resulting thread still chunks normally afterward.
4. **Ingest-side backpressure is already there, reused as-is.** The
  `INGEST_MAX_RSS_MB` memory admission gate (worker skips claiming new work
   under memory pressure) and `INGEST_CONTEXTUAL_CONCURRENCY`-bounded
   contextualization apply to *any* `ingest_source` call regardless of
   provider — a Slack sync producing an unusually large number of documents
   degrades exactly like an unusually large Notion export would: throttled,
   not crashed. No Slack-specific worker change needed here.
5. **Contextual retrieval off by default, plus a whole-sync chunk cap (D11).**
  Skipping `INGEST_CONTEXTUAL_ENABLED` for Slack removes the single biggest
   per-chunk cost (one LLM call each) that the other four bounds don't touch
   at all — they cap document count and size, not the cost of enriching each
   resulting chunk. `SLACK_MAX_CHUNKS_PER_SYNC` then caps the aggregate chunk
   count across an entire sync, which #1-#3 bound per-channel/per-thread but
   not in total. See D11 for the full reasoning.

Net effect: a channel's *history* is bounded (#1), its *noise* is filtered
before it costs an embedding call (#2), a single pathological thread is
bounded (#3), the worker's existing global safety net still applies (#4),
and per-chunk LLM cost plus the sync-wide chunk total are both capped (#5,
D11) — no single new mechanism carries the whole weight, matching how every
other volume risk in this codebase (Notion fetch size, GitHub diffs, chunk
character ceiling) was closed by stacking several small bounds rather than
one clever one.

## 7. Personal / workspace-scoped Slack connections (doubt #3)

The ask: an employee should be able to connect *their own* Slack channels
(e.g. a small team's private planning channel) into a personal
Workspace-within-a-Workspace, invite a few colleagues, and have questions in
that space answered only from those channels — the same shape Notion, Drive,
and GitHub already support at workspace scope.

**The wrinkle Slack has that the others don't:** Notion/Drive/GitHub each let
the *connecting person's own OAuth grant* naturally scope access (a personal
Notion integration, the user's own Drive folder, GitHub's
`prefer_user_account` install). Slack's bot, once installed by an org admin
(D6), can already see every channel it's been invited to — there's no
Slack-native way for "employee A's connect action" to be inherently narrower
than "employee B's connect action," because both ride the same shared bot
token. Left unaddressed, this would mean a workspace owner could point a
personal space's Slack connection at ANY channel the bot happens to be in,
including ones they aren't personally part of — a real access-boundary gap
directly parallel to the one GitHub's plan already closed (§4 there: "a
workspace GitHub connect must never land on the org's installation").

**The fix (D9): a lightweight identity check, not a second bot.**

```
1. Workspace owner opens their workspace → Connections → "Connect Slack".
      → GET /auth/slack/authorize?workspace_id=<id>
      -> if the org has NO Slack connection yet, this bootstraps the shared
         bot install first (step 2-3 of §5) — one org can only ever have one
         Slack bot token, workspace or not (D6).
      -> if the org connection ALREADY exists, this step is a "Sign in with
         Slack" identity-only grant (scope: identify / users:read on the
         REQUESTING USER, not a new bot install) — proves "this Slack account
         belongs to this employee."

2. Callback resolves the Slack user_id from the identity grant and calls
   conversations.list, filtering to channels where that user_id is a member
   (via each channel's own membership, not just "the bot is a member").
   Presents the SAME channel-picker UI as §5, step 4, but pre-filtered: only
   channels this specific person actually belongs to appear at all — never
   the full list the shared bot can see.

3. Rows still show "Ready" vs. "Invite the bot" (D7) exactly as org-level —
   being a member yourself doesn't imply the bot is also in the channel yet.

4. Save writes channel_ids into THIS workspace's oauth_connections row's
   source_config (a workspace-scoped row, per the existing
   idx_..._workspace partial index) — never the org-wide row.

5. D10's guard: reject (with a clear error) selecting a channel that's
   already registered under a DIFFERENT workspace_id (or org-wide) for this
   org — same channel, two owners, is a redundancy bug, not a feature.
```

**What this buys, and its honest limit:** it stops a workspace owner from
*unilaterally* pointing at a channel they don't belong to, using Slack's own
membership as ground truth — the same trust anchor GitHub's
`resolve_repo`/`installation_repositories` and Drive's `files.get` validation
already use ("check against the provider's own record of what's actually
authorized," `CLAUDE.md` §2). It is **not** full per-user ACL (D1/§0's
rejected scope) — once a channel is connected to a workspace, every member
of *that workspace* can query it, same as every other workspace-scoped
source today; it only gates *who may connect a given channel in the first
place*, not who may later read it once connected (that part is exactly the
existing workspace-membership boundary, unchanged).

## 8. Configuration — turning this on (doubt #4)

Concretely, what has to exist before any of the above works, mirroring how
`docs/plans/2026-08-05-github-integration.md` needed a GitHub App:

1. **Register a new Slack App** at api.slack.com/apps (one App per
  deployment, own credentials — same posture as GitHub's own App, per open
   question below). Configure:
  - **OAuth & Permissions → Bot Token Scopes**: `channels:history`,
  `channels:read`, `channels:join`, `groups:history`, `groups:read`,
  `im:history`, `im:read`, `mpim:history`, `mpim:read`, `users:read`.
  - **OAuth & Permissions → Redirect URLs**: `https://<frontend>/api/auth/slack/callback`
  (same first-party-cookie-safe pattern the GitHub/Google callbacks
  already use, `CLAUDE.md` §4 — never point this at the Render host
  directly).
  - **"Sign in with Slack" (OpenID Connect) scopes** (for D9's identity-only
  grant): `openid`, `profile` — a *separate*, much narrower scope set from
  the bot scopes above, requested only during a workspace-scoped connect.
  - No Event Subscriptions, no Interactivity, no Slash Commands needed (D4,
  §6 non-goals) — leave those off entirely; a smaller configured surface
  is also a smaller thing to audit later.
2. **New env vars** (`app/config/settings.py`, new `SlackSettings`, same
  `.from_env()` dataclass convention as every other settings block):
   `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_REDIRECT_URI`,
   `SLACK_BACKFILL_DAYS` (default 90), `SLACK_MIN_THREAD_CHARS` (default 40),
   `SLACK_MAX_THREAD_MESSAGES` (default 50), `SLACK_MAX_CHUNKS_PER_SYNC`
   (default 20,000, D11). No signing secret needed in v1 (that's only
   required for verifying inbound Events API webhooks, which §6 deliberately
   excludes).
3. **Wiring, all additive, no existing factory signature changes:**
  `app/auth/factory.py` gains a `"slack"` branch returning
   `SlackOAuthProvider`; `app/sources/factory.py` gains a `"slack"` branch
   returning `SlackAdapter`; `app/api/auth.py` gains
   `/auth/slack/authorize|callback` following the exact shape of the
   Notion/Google/GitHub routes already there (state creation/consumption,
   `save_connection`, redirect-to-picker); two new small endpoints
   (`GET`/`PUT /admin/connections/{id}/slack/channels` and the workspace
   equivalent under `/workspaces/{id}/connections/{cid}/slack/channels`) for
   the picker in §5/§7.
4. **Nothing to configure in** `app/rag/`**,** `app/ingestion/`**, or the DB** beyond
  what §3/§4 already cover — nothing in the retrieval/gate/prompt path knows
   or needs to know Slack exists.



## 9. Non-goals (v1), each deliberate

- **Per-user permission-aware retrieval** (Glean/Onyx's headline feature) —
rejected in D1/§0: it's solving a problem (per-Slack-user visibility) this
project doesn't model anywhere yet, and bolting it on only for Slack would
be a new, inconsistent access-control axis.
- **Real-time Events API / Socket Mode ingestion; Event Subscriptions,
Interactivity, Slash Commands in the Slack App config** — v1 is poll-based
only (D4), matching Notion/Drive's existing freshness model; the App is
configured with none of that surface enabled at all (§8.1).
- **DMs and group DMs** — the channel picker only lists public/private
*channels*; a bot being added to someone's DMs is a much sharper privacy
line than a shared channel and isn't asked for here. Revisit only on
explicit request.
- **File/attachment content** — a shared file's *name* appears in the
rendered thread text; its *contents* are not fetched or embedded (v1).
- **Slash commands / posting back to Slack /** `chat:write` — this is a
read-only connector, matching how Notion/Drive are read-only sources.
`chat:write` scope is not requested.
- **Full per-user permission-aware retrieval inside a connected channel**
(Glean/Onyx's headline feature) — D1/§0 already rejected this at the org
level; §9's D9/D10 add a connect-time membership check, which is a
narrower, deliberately different guarantee (who may *connect* a channel,
not who may *read* it once connected — see §9's "honest limit").



## 10. Open questions for sign-off before implementation

1. Default backfill window (`SLACK_BACKFILL_DAYS`) — 90 days proposed, needs
  a number someone's comfortable with for a first sync's latency (§6.1).
2. Default noise-filter/thread-size constants (`SLACK_MIN_THREAD_CHARS`=40,
  `SLACK_MAX_THREAD_MESSAGES`=50, §6.2/§6.3) — reasonable starting points,
   not yet validated against a real workspace's message-length distribution.
3. Confirm the Slack App will be a net-new App (own
  `SLACK_CLIENT_ID`/`SECRET`), same posture as GitHub's own App credentials,
   rather than reusing any existing App registration.
4. §9's identity-only "Sign in with Slack" grant needs a UX call: does a
  workspace owner see this as an extra, separate consent screen every time
   they open the channel picker, or is it requested once and cached (subject
   to Slack's own OIDC token lifetime)? Leaning toward once-per-connection,
   re-prompted only if Slack's identity token has actually expired.

