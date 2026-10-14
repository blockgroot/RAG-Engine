# Write Actions Plan

> **Status (2026-10-14): Phases 0–3 BUILT** on `feat/write-actions-plan` (not merged). Off until a company's admin allows Act (no env switch, decided 2026-10-14). Research sources are
> listed in §10. Facts marked *(unverified)* were not confirmed in a primary doc.

**Goal:** an employee can ask the assistant to *do* something in a connected tool:
file a Linear issue, comment on a GitHub PR, post to a Slack channel, add to a
Notion page, create a Google Doc. The write must be exactly what they saw and
approved, run with **their own** permissions in that tool, and be recorded.

**Architecture in one line:** the model may only *propose* an action. Code
resolves the target from trusted inputs, the asker approves a frozen preview,
and code executes it once with the asker's own token, then logs it.

---

## 1. Where we start (code as of `e4b9cca`)

| Area | Today | Why it matters for writes |
|---|---|---|
| Scopes | Linear `read` (`settings.py:701`), Google `drive.readonly` + `documents.readonly` (`settings.py:775`), Slack bot scopes incl. `chat:write`, `files:write` (`settings.py:882`), GitHub App permissions set on github.com, Notion capabilities set in its dashboard | Everything except the Slack bot is read-only |
| Who a call runs as | One `oauth_connections` row per (org, provider) or (org, provider, workspace) (`schema.sql:424-467`). Calls run as the connecting admin (Google, Linear), the Notion integration, the Slack bot, or the GitHub App installation | A write with these tokens would act **with the admin's or the bot's rights**, not the asker's (confused deputy) |
| Person-bound OAuth | Only "Link your GitHub account": `oauth_states.user_id` exists (`schema.sql:664`), but `identify_user` reads `/user` and **discards the token** (`github_oauth.py:274-306`) | The state plumbing for per-person OAuth already exists. We would now keep the token |
| `connected_by_user_id` | Column exists and is never set for source connections (`auth.py:316`, `auth.py:527`) | Fix it in Phase 0: an audit trail needs it |
| Tool calling | `generate_with_tools` (`llm/base.py:56`). One call, then `tool_calls[0]`, then if/elif dispatch (`github_agent.py:357-470`). Tools are JSON-schema constants, with no registry | Fits "propose one action". No loop is needed |
| The model never names a raw target | Opaque handles `[L1]` (`livetools/handles.py:18-39`); `resolve_repo` (`githublive/repos.py:169`); `user_worded_query` (`security/outbound.py:25`); `enforce_link_provenance` (`security/links.py:55`) | These are the primitives for "targets come from trusted input" |
| Model-driven writes | `POST /schedulers/setup-chat` creates a scheduler from a tool call with no confirmation (`api/schedulers.py:555-650`) | It writes only our own DB and the user is in a guided setup chat, so it stays as is. External writes are a different risk |
| Roles | Org `admin`/`member`; workspace `owner`/`member`; `require_admin`, `require_workspace_owner` (`deps.py:188-219`) | Enough for an "enable actions" policy. No new role system |
| Audit | `live_tool_calls` (`schema.sql:1325`), best-effort, purged after 90 days | A write log must **not** be best-effort |
| Rate limit | `check_rate_limit` (`security/rate_limit.py:24`) | Reuse it per user per action |
| Chat stream | `status` / `token` / `done` / `error` (`api/chat.py`). Frontend `sse.ts:145-152` ignores unknown events. `done` is persisted to `conversation_turns.meta` | A proposal can ride on `done`, with no new event type |
| Slack bot | Signed events, posts and updates replies, uploads chart PNGs. **No interactivity**: no buttons and no `block_actions` endpoint | Approve/Cancel in Slack needs a new endpoint |

---

## 2. How production systems do it

| System | Runs as | Confirmation | Admin control | Audit |
|---|---|---|---|---|
| **Onyx** (open source; read at `587a4a1`) | Per tool: per-user OAuth (`oauth_user_token`), passthrough login token, or a shared admin header. MCP: `MCPAuthenticationPerformer` ADMIN or PER_USER | **None in chat or the Slack bot.** `tool_runner.run_tool_calls` calls `tool.run()` directly, and MCP `readOnlyHint`/`destructiveHint` are ignored there. Only "Craft" (the sandbox) gates writes, through an egress proxy with ALWAYS/ASK/DENY per action: ASK by default, the strictest policy wins, and an unclassifiable call is DENY | `MANAGE_ACTIONS` permission; tools attached to agents (`persona__tool`); MCP servers per user or group | `tool_call` table stores args and response. The SIEM audit stream has no `tool.*` events. Decisions go to `action_approval` (Craft only) |
| **Glean Actions** | User | Every write is confirmed by default. "Run without confirmation" is allowed only for background or scheduled agents, never interactive ones | Admin enables per action or pack, plus RBAC | Yes |
| **M365 Copilot** | User | Every action is confirmed. "Always allow" is offered only for GET; `isConsequential` removes it | Admin | Yes |
| **Atlassian Rovo** | User (can do only what the user can do) | Every action is confirmed; deletes are confirmed twice | Admin | Yes |
| **ChatGPT apps / MCP** | Per-user OAuth | Writes are confirmed and the full JSON is shown. A tool without `readOnlyHint` counts as a write. Approval can be remembered per tool per conversation | Workspace admin | — |
| **Dust** (open source) | Per-user "personal actions" OAuth | Stake levels high / medium / low / never_ask; an unknown MCP tool defaults to **high** | Admin sets the level per tool | Yes |
| **LibreChat / Open WebUI** | Per-user OAuth for MCP | No built-in per-call approval | Per agent on/off | — |

**What we take:**
- per-user tokens (all of them)
- confirmation required for every interactive write, with no "always allow" in v1 (Glean, M365, Rovo)
- unknown or unclassifiable means deny (Onyx Craft)
- a decision row with payload and outcome (Onyx `action_approval`)
- admin enables each action (Glean, Dust)

**What we avoid:**
- Onyx's chat path, where an injected document can trigger a write with no human in the loop
- trusting a tool's self-declared read-only hint (the MCP spec itself says annotations from untrusted servers are untrusted)

**Security frame.** Every Ask carries three things together:
- private data
- untrusted retrieved text
- now, a channel that changes state or reaches the outside

That is Willison's "lethal trifecta" and Meta's "Rule of Two" violated, and OWASP LLM06 "Excessive Agency" applies. With all three present, a human approval is mandatory, not a UX preference. Approval alone is still not enough: if a retrieved passage chooses **where** the write goes, the user is approving an attacker's target. Hence §4.3.

---

## 3. Decisions

1. **Writes use the asker's own token in that tool, never the org connection.** Reads stay as they are. A new `user_connections` table holds one row per (org, user, provider). It is created the first time the person tries to write ("Connect Linear to let the assistant act for you"), with write-only scopes. The provider then enforces the person's real permissions and shows them as the author.

   | Provider | Write identity | Scopes / permissions | Who it shows as |
   |---|---|---|---|
   | Linear | User OAuth, `actor=user` | `read,issues:create,comments:create` (never `write`/`admin`) | The person |
   | GitHub | GitHub App **user-to-server** token, narrowed per write to one repo + `issues: write` | App gains **Issues: write only**. No Pull requests: write (it would allow approving, editing and closing PRs as the person; a PR comment is an issue comment). No Contents: write | The person, with the App badge. Can do only what both the person and the App can do |
   | Notion | Per-user **public OAuth** ("acts on behalf of the individual user who authorized them") | Insert content, Update content, Insert comments | Comments use `display_name: {type: "user"}`. Page edit attribution *(unverified)* |
   | Google | Per-user OAuth, **`drive.file`** only, added through incremental consent | `drive.file` (non-sensitive) | The person |
   | Slack | **The org's bot**, not a user token | Existing `chat:write` | The bot, with "on behalf of @person" in the message |

   Slack is the exception on purpose:
   - Marketplace rules say "DON'T impersonate a user", and user-token write scopes get extra review.
   - A user token would also let the assistant post anywhere the person can, which is too wide.

   Instead the bot posts only into a channel where code has confirmed **both** the bot and the asker are members (`conversations.members`), and it names the asker in the message.

2. **No Google restricted scope.** Editing arbitrary Drive files would need `drive` (restricted, with a yearly third-party security assessment, CASA). `drive.file` covers files we create and files the person picks in Google Picker. That is the whole Google surface.

3. **Every interactive write is confirmed, every time.** There is no "always allow" in v1. Background writes (schedulers, digests) are out of scope: a write never runs without a person present.

4. **Default off, at three levels:**
   - the company's admin allows Act at all (the `act` row in `action_policies`, `policy.act_on`; no env switch, decided 2026-10-14)
   - org admin enables each action
   - a workspace owner may switch actions **off** for their space, never on beyond the org's setting

5. **Create and comment only in v1.** No edit-in-place of someone else's content, no delete, no close, no merge. Each later action is its own decision.

6. **Act is its own mode, built and shipped separately.** Ask and Chart never propose a write, and their code never imports `app/actions/`.
   - Act has its own UI toggle (like Chart mode), its own route (`POST /act/stream`), its own package and its own tests.
   - A bug in Act cannot change an Ask or Chart answer, and turning Act off removes it completely.

7. **A write is triggered only when the person explicitly asks for one.** In Act mode the model must quote the words in the question that asked for the write (`request_words`), and code checks that the quote is really in the question. This is the same pattern as `breakdown_words` in charts.
   - No quote means no proposal, only an answer explaining what Act can do.
   - The model never offers a write on its own initiative ("Want me to file this?").

8. **Some things are impossible by construction, not by prompt.** See §4.0.

---

## 4. Design

### 4.0 Guardrails: what the model can never do

Each rule is enforced in code or by the provider, never only by the prompt.

| Rule | Enforced by |
|---|---|
| The model holds no token and makes no HTTP call. It only fills in one action's form | `Action.execute()` runs in the confirm endpoint, after approval, outside any model call |
| A fixed list of actions; there is no "call any API" or MCP passthrough | `factory.build_actions` returns only classes in `app/actions/`. An unknown tool name is dropped |
| Never push code, never touch `main` or any branch, never merge or approve | The GitHub App gets **no `Contents: write`** (merge, refs, commits and git push all require it) and **no `Pull requests: write`** (approve/edit/close). Each write token is narrowed to one repo and `issues: write`. No action class calls git refs, contents, pulls or merge endpoints |
| No deletes, no closing, no changing sharing or permissions, no inviting people, no admin actions | Not in the catalogue. Write scopes are create/comment only (Linear `issues:create,comments:create`; never `write`/`admin`) |
| One action per approval: no chaining, no loops, no follow-up writes from a write's result | One `generate_with_tools` call yields at most `tool_calls[0]`. `execute()` returns a result, never a new proposal |
| Nothing runs without a person approving it now | Only `POST /actions/{id}/confirm` by the same user, before `expires_at`. No scheduler, digest or webhook path imports `execute` |
| Only an explicit request starts a write | `request_words` quote check (Decision 7) |
| The target comes from the person, not from documents | §4.3 handle minting |
| What runs is exactly what was shown | Stored payload + `payload_hash`; atomic `proposed → executing` |
| Volume is capped | Per-user hourly rate limit, per-org kill switch, env kill switch |

### 4.1 Package: `app/actions/`

Follows the CLAUDE.md convention: `base.py` + impls + `factory.py` + `__init__.py`. It is one capability with one implementation per provider, like `app/agent/`.

```
app/actions/
  base.py       Action ABC, ActionTarget, Proposal, ActionResult
  linear.py     create_issue, add_comment
  github.py     comment_on_issue_or_pr, create_issue
  notion.py     add_comment, append_to_page, create_child_page
  slack.py      post_message
  drive.py      create_doc
  factory.py    build_actions(org_id, workspace_id, user_id) -> enabled Actions
  store.py      pending_actions + action_log SQL
  policy.py     is_enabled(org, workspace, action); rate limit
  __init__.py
app/auth/user_connections.py   save/get/refresh/delete per-person tokens
app/api/actions.py             confirm / reject / list; per-user connect
```

```python
class Action(ABC):
    name: ClassVar[str]           # "linear.create_issue"
    provider: ClassVar[str]       # "linear"
    tool: ClassVar[dict]          # JSON schema shown to the model; targets are handles
    destructive: ClassVar[bool] = False

    @abstractmethod
    def prepare(self, args: dict, ctx: ActionContext) -> Proposal:
        """Validate model args, resolve handles to real targets, render the exact
        payload. Raises ActionRejected; never calls the provider to write."""

    @abstractmethod
    def execute(self, proposal: Proposal, token: str) -> ActionResult:
        """One write, with the asker's token. Returns external id + http(s) url."""
```

Exceptions are `ActionError(ProviderError)`, with subclasses `ActionRejected`, `ActionExpired`, `ActionConflict` and `ActionOutcomeUnknown`. Each takes `cause=` and uses `raise ... from`.

Config is a frozen dataclass `ActionsSettings.from_env()` in `settings.py`:
- `ACTION_TTL_SECONDS=900`
- `ACTION_RATE_PER_HOUR=20`
- per-provider write scopes

### 4.2 Data

`ALTER`s follow their table's `CREATE TABLE` in `schema.sql`.

```sql
-- A person's own token for acting in a tool. Never used for reads or sync.
CREATE TABLE IF NOT EXISTS user_connections (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                  UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    user_id                 UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    provider                TEXT NOT NULL,
    external_user_id        TEXT NOT NULL,      -- Linear user id, GitHub login, Notion user id, Google sub
    scopes                  TEXT[] NOT NULL,    -- what was GRANTED (Google lets people untick)
    access_token_encrypted  TEXT NOT NULL,      -- Fernet, as oauth_connections
    refresh_token_encrypted TEXT,
    expires_at              TIMESTAMPTZ,
    needs_reauth            BOOLEAN NOT NULL DEFAULT false,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, user_id, provider)
);

-- Which actions an org (workspace_id NULL) or a space has switched on.
CREATE TABLE IF NOT EXISTS action_policies (
    org_id       UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES workspaces (id) ON DELETE CASCADE,
    action       TEXT NOT NULL,
    enabled      BOOLEAN NOT NULL,
    updated_by   UUID REFERENCES users (id),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_policies_orgwide
    ON action_policies (org_id, action) WHERE workspace_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_action_policies_workspace
    ON action_policies (org_id, workspace_id, action) WHERE workspace_id IS NOT NULL;

-- One proposed write, from proposal to outcome. Never deleted (the audit row).
CREATE TABLE IF NOT EXISTS actions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
    workspace_id    UUID REFERENCES workspaces (id) ON DELETE SET NULL,
    user_id         UUID REFERENCES users (id) ON DELETE SET NULL,
    conversation_id UUID,
    surface         TEXT NOT NULL,          -- 'web' | 'slack'
    action          TEXT NOT NULL,          -- 'linear.create_issue'
    target          JSONB NOT NULL,         -- resolved: {kind, external_id, name, url}
    payload         JSONB NOT NULL,         -- exactly what will be sent
    payload_hash    TEXT NOT NULL,          -- sha256(canonical json of action+target+payload)
    sources         JSONB NOT NULL DEFAULT '[]',  -- documents the draft drew on: {document_id, title, public, viewers}
    exposure        TEXT NOT NULL DEFAULT 'none', -- none|narrower|wider|unknown|public (§4.3a)
    acknowledged    BOOLEAN NOT NULL DEFAULT false, -- the person ticked the widening warning
    status          TEXT NOT NULL,          -- proposed|executing|succeeded|failed|unknown|rejected|expired
    expires_at      TIMESTAMPTZ NOT NULL,
    decided_at      TIMESTAMPTZ,
    executed_at     TIMESTAMPTZ,
    result          JSONB,                  -- {external_id, url} or {error} — never a token
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_actions_org_time ON actions (org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_actions_user_status ON actions (org_id, user_id, status);
```

- One `actions` table is both the pending queue and the audit log. Rows are never deleted, and `ON DELETE SET NULL` keeps the history after a member is removed.
- Removing a member cascades their `user_connections`, which revokes their write ability immediately.
- Org deletion cascades everything, as on every tenant table.

### 4.3 Where targets come from (the injection rule)

The model fills a tool schema whose target fields are **handles**, never ids or URLs. Code resolves each handle against a map minted **for this request** from trusted sources only:

| Target kind | Minted from |
|---|---|
| Linear team, GitHub repo, Slack channel, Notion page | (a) the user picked it in a UI picker, **or** (b) its name appears in the asker's own question (`user_worded_query`-style check), **or** (c) it is a document the asker can see (`visibility_predicate`) **and** their question refers to it ("this thread", a page they named). Never a target that only appears inside retrieved text |
| An issue or PR to comment on | The same, plus `resolve_repo` for GitHub |
| Recipients / @-mentions | Only the asker's own words. Mentions found in retrieved text are stripped |

Payload text (title, body) **may** be drafted from retrieved passages, since summarising is the point. Two limits apply:
- **Links:** only URLs that appear verbatim in the asker's own messages, or a cited document's `source_uri`. This is a stricter variant of `enforce_link_provenance`; a model-written URL is how a write becomes an exfiltration channel.
- **Size:** bounded per provider (title ≤ 255, body ≤ 8,000 chars). It is cut with a visible "(trimmed)" marker, never silently.

The proposal prompt carries retrieved text, so it uses `UNTRUSTED_POLICY` / `UNTRUSTED_REMINDER`, and `tests/test_untrusted_policy.py` covers it.

### 4.3a Private content reaching a wider audience

**The gap.** Priya can read a private HR doc. She asks Act to "post a summary of the reorg doc to #general". Every other check passes: she asked, she is a member, she approved. But content 5 people could see now reaches 300. Approval does not catch this well, because people approve what they asked for.

**The rule.** Every proposal records the documents its draft drew on (`actions.sources`), using the same hits the viewer was shown, each with `doc_is_public` and `doc_viewers`. `prepare()` then compares that with the destination's audience and stores a verdict in `actions.exposure`:

| Verdict | When | What happens |
|---|---|---|
| `none` | No source, or every source is public | Normal card |
| `narrower` | Every person in the destination audience is a viewer of **every** private source | Normal card |
| `wider` | The destination audience is known and includes someone who cannot see a private source | **Warning on the card, and Approve stays disabled until the person ticks** "I understand this shares content from private documents with people who can't see them". The tick is stored (`acknowledged`) and the confirm endpoint refuses without it |
| `unknown` | The destination audience cannot be read (Notion page, private GitHub repo without collaborator access, a Linear team whose members can't be listed) | Same as `wider`: warning plus tick. An unknown audience is never treated as safe |
| `public` | The destination is readable by everyone in the company or the world (a public Slack channel, a public GitHub repo) | **Blocked.** No proposal is stored; the answer says why and suggests a private destination or drafting without the private sources |

The card names the facts, never just "warning": *"Uses 2 private documents (Reorg plan, HR budget), visible to 5 people. #leads has 41 members, 36 of whom can't see them."* Titles shown are only those the asker can already see.

**Destination audiences,** read at `prepare()` time with the asker's own token, bounded and marked truncated (a truncated member list counts as `unknown`):
- Slack channel: `conversations.members`. A public channel is `public`.
- Linear team: the team's members.
- GitHub repo: public means `public`; private means `unknown` unless the collaborator list is readable.
- Notion page: `unknown` (no permission API).
- A new Google Doc in the person's own Drive: only them, so `narrower`.

Emails are compared lowercased, through the same alias table the `Viewer` uses.

**Why warn-with-tick for `wider` and not block.** Turning a private Slack thread into an issue for the same team is the most common legitimate use, and the team rarely matches the thread's membership exactly. Blocking every `wider` case would make Act useless for its main job. A public destination is different: it cannot be undone, and its audience includes everyone. An org setting `ACTIONS_BLOCK_WIDER` turns `wider` and `unknown` into blocks for orgs that want it.

### 4.4 Flow, web chat

```
Act mode (separate toggle) ──► POST /act/stream
          │
          ▼
        retrieval for context (same gate, same viewer, read-only)
          │
          ▼
        action agent: ONE generate_with_tools call over the
        actions enabled for this org/space AND connected by this person
          │  tool_calls[0]
          ▼
        request_words quote in the question?  (else: no proposal)
          │
          ▼
        Action.prepare()  ── validate, resolve handles, render payload
          │                  rejects → normal answer explaining why
          ▼
        INSERT actions (status=proposed, payload_hash, expires_at=now+15m)
          │
          ▼
        `done` event carries  proposed_action: {id, action, target, payload,
        payload_hash, expires_at}   (also saved in conversation_turns.meta)
          │
          ▼
  user clicks Approve ──► POST /actions/{id}/confirm {payload_hash}
          │
          ▼
        server, in order:
          1. row belongs to session org_id + user_id   (else 404)
          2. status == proposed AND not expired        (else 409 / expired)
          3. hash matches                              (else 409)
          3a. exposure wider/unknown → acknowledged     (else 409); public never stored
          4. policy still enabled; rate limit
          5. user_connection present, not needs_reauth (else "Reconnect")
          6. UPDATE … SET status='executing' WHERE id=$1 AND status='proposed'
             RETURNING *          ← the single-execution guarantee
          7. Action.execute() with the person's token (refresh if near expiry)
          8. status = succeeded | failed | unknown;  result = {id, url}
```

Notes:
- **No missing-connection path.** If the person has no `user_connection` for the provider, the action agent does not offer that action. The answer says "Connect Linear to let me file this for you" and links the per-user connect flow.
- **Approval binds to the payload.** The card shows the payload, and the server executes the stored payload, never one re-sent by the client. The hash proves the user saw this version.
- **Timeouts are not retried.** A timeout or 5xx after sending is `unknown`. The card says "may have been created, check Linear" and links the target. Duplicates are worse than a manual check.
  - Linear is the exception: `IssueCreateInput.id` and `CommentCreateInput.id` take a client UUID ("If none is provided, the backend will generate one"). We pass `actions.id`, so a repeated send cannot create a second issue.
- **Reject and expire.** Reject sets `rejected`. An expiry sweep in the existing worker tick sets `expired`, with a bounded batch per tick (a CTE, per CLAUDE.md).
- **Editing.** The card offers no edit in v1. The person cancels and rephrases. An edited body would come from the person, so it would be safe to add later.

### 4.5 Slack

Phase 3. It needs an Interactivity Request URL (`POST /slack/interactivity`, same signing check as `/slack/events`).

- **Preview.** The bot replies with an **ephemeral** preview (`chat.postEphemeral`, visible to the asker only) and Approve / Cancel buttons. `value` holds only the `actions.id`.
- **On `block_actions`:**
  - Verify the signature.
  - Map `payload.user.id` to an email, then to our user. Fix the alias gap first: today the mapping uses an exact `users.email` match; use `user_email_aliases` too.
  - Require that user to equal `actions.user_id`.
  - Then run the same confirm path as the web.
  - Ack within 3 s, execute in the background, and replace the preview through `response_url` (valid for 30 min, 5 uses).
- **A channel mention never posts to another channel.** In a channel, the only allowed Slack target is the current thread.

### 4.6 Frontend

- **Action card.** `ActionCard.tsx` sits under the answer, next to `RememberedNote` (the existing after-the-fact pattern). It shows:
  - the target (name + link)
  - the exact payload
  - "as you (Linear)" or "as Handbook, on behalf of you (Slack)"
  - Approve and Cancel, plus a countdown
  
  After the write, the card links to the created item. Reopening a chat shows the stored status from `meta`.
- **Settings › Connected for actions.** Per-person connect and disconnect for each provider, with the granted scopes listed.
- **Admin › Actions.** One toggle per action, a list of recent `actions` rows for the org, and a filter by user.

---

## 5. Per-provider notes

**Linear.**
- Add a second OAuth app or client configuration for user write tokens (`actor=user`, `issues:create,comments:create`). Linear suggests separate tokens per actor mode.
- Do not widen the org read connection.
- **Verified:** any workspace member may create issues in a **public** team without joining it; a **private** team's issues exist only for its members; guests see only their own teams. Linear documents no specific error for an inaccessible team, so `prepare()` resolves the team against the root `teams` query run with the asker's token ("public teams and private teams the user is a member of") and refuses anything else before sending.

**GitHub.**
- **One App, least privilege per token (decided 2026-10-14).** The App gains Issues: write only. Every installation owner must approve it; until they do, "the installation will continue to use the old permissions", reads keep working, and `company_repo` refuses with "Your GitHub admin hasn't accepted…" (read live from `GET /app/installations/{id}`).
- **Reading tokens are read-only:** `mint_installation_token` asks for every granted permission at `read` (`workflows` has no read level and is left out), so the sync and live-read paths cannot write even though the App can. Fails closed: no grant read, no token.
- **Each write is narrowed:** right before sending, the person's token is exchanged at `POST /applications/{client_id}/token/scoped` for one limited to the one repo and `issues: write` (`actions/github.py::scoped_token`). A refusal there means nothing is sent.
- Store the user-to-server token from the link flow. It lasts 8 h with a 6-month refresh token; keep expiry on.
- **A refresh token is single-use**: "Once you use a refresh token, that refresh token and the old user access token will no longer work." Refresh holds a row lock (`SELECT … FOR UPDATE` on the `user_connections` row) and saves the new pair in the same transaction, so two requests cannot both refresh and break the connection.
- Commenting on a PR uses the issues comments endpoint, which accepts Issues: write ("at least one of" Issues or Pull requests write).
- "Open a PR" (Contents: write) is out of scope.

**Notion.**
- **A SEPARATE public connection, "Handbook Actions" (decided 2026-10-14).** Notion capabilities belong to the connection, not to an authorization: the authorize URL takes no scope, and "you receive an `access_token` with the capabilities your connection requested". Adding write to the read connection would give the admin's org-wide indexing token write over every page it was shared, and show write capabilities on every admin's consent screen. GitHub can narrow a token per request; Notion cannot, so the split must be two connections. It costs users nothing: each person connects Act separately anyway.
  - "Handbook" (existing): read content only, connected by admins, unchanged.
  - "Handbook Actions" (new): read content (finding the named page: `GET /pages`, `/search`), insert content, insert comments, read comments. Update content only if an edit action ever ships. Read content reaches only the pages the person picked when connecting. Its own `NOTION_ACT_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI`.
- Per person: "Public connections act on behalf of the individual user who authorized them". The person picks the pages in Notion's picker, so writes reach only pages they shared.
- **Attribution.** Comments: verified. We send `display_name: {"type": "user"}`, which shows the person's name. Pages and blocks: not documented; every sign (`/v1/users/me` is "your token's bot user", the token carries a `bot_id`) points to the connection's bot. The card says "added via Handbook Actions, for you" for page writes, never "as you", until a live test proves otherwise.
- **Edit rights are not exposed by the API** (no permission field on a page). Whether a person with only "can comment" can write through the connection is not documented. So `prepare` also requires the target page to be visible to the asker in our index (`visibility_predicate`), and a 403 `restricted_resource` / 404 `object_not_found` is shown as "you can't edit that page".
- **Live test before Phase 2 ships** (needs a test workspace): create a child page, append a block and post a comment as a can-comment-only user; record `created_by`, page history and whether Notion refused.
- Rejected: Personal Access Tokens (Notion: "Don't use a PAT to authenticate a product used by many Notion users"; no OAuth, pasted tokens; owners-only by default on Business and Enterprise). Notion's hosted MCP server acts as the user, but server-side multi-tenant use and attribution are not confirmed and Enterprise admins can block unapproved clients. Worth a later test, not the plan.

**Google.**
- Separate per-user OAuth: `drive.file`, `access_type=offline`, `include_granted_scopes=true`.
- Check the granted scopes, because people can untick.
- v1 creates a Doc in the person's own Drive, optionally inside a folder they pick.
- Editing an existing file requires that the person picked it in Google Picker. That is Phase 4.

**Slack.** No new scopes. Membership checks use `conversations.members`, which works only for channels the bot is in; a channel the bot isn't in is refused. Do not request `chat:write.public` or `chat:write.customize`.
- The write design holds listed or unlisted: bot token, text naming the person ("DO clearly attribute app actions to a user"; "DON'T impersonate a user"), an ephemeral preview, and Approve checked against the stored requester (`user.id` and team), single-use, expiring within `response_url`'s 30 minutes.
- **Found while researching, bigger than Act (2026-10-14):** Slack's API Terms say "You may not Commercially Distribute an Application ... unless you are authorised to do so under a separate agreement", which for most apps "simply means submitting to our Marketplace"; "Commercially Distribute" includes any app tied to a paid product. And since 29 May 2025, a NEW install of an unlisted commercial app gets `conversations.history` / `conversations.replies` at 1 request per minute and 15 messages, which breaks indexing for every new customer (existing installs are exempt per the current docs). A listing keeps indexing allowed as long as the Agent/Assistant UI is NOT enabled (its rule is "DON'T store any Slack data"); the review looks hard at `*:history` scopes, the 90-day free-plan limit, sentiment features and LLM disclosures. Decision pending with the product owner.

---

## 6. Phases

Each phase ships behind the org's "Allow Act" switch and the per-action toggles. Per our test practice, each phase runs **only its own new test file**.

**Phase 0: groundwork (no user-visible change)**
- Add `user_connections`, `actions`, `action_policies`.
- `app/actions/` skeleton, `ActionsSettings`, exceptions.
- `app/auth/user_connections.py`: save, read and delete a person's write token, encrypted like `oauth_connections`.
- The exposure verdict (§4.3a) as a pure function, so every later phase uses the same one.
- Tests: `tests/test_actions_store.py`:
  - single execution under concurrent confirms
  - org and user isolation on every query
  - an expired proposal can never execute
  - hash mismatch is refused
  - `wider`/`unknown` without the tick is refused; `public` is never stored
- Kept **out** of Act, so it stays a separate piece of work:
  - Setting `oauth_connections.connected_by_user_id` touches the shared GitHub connect flow. It is a separate cleanup.
  - The Slack alias fix moves to Phase 3, where Slack approval needs it.

**Phase 1: Linear + GitHub on the web — BUILT (2026-10-14)**
- Per-person connect: `GET /act/connect/{linear|github}`. The state is bound to the person (`linear_act` / `github_act`) and finished from the provider's existing callback (`api/actions.py::finish_act_connect`). Linear asks for `read,issues:create,comments:create`; a Linear account from another Linear workspace than the company's is refused. GitHub keeps the user-to-server token (`GitHubAppProvider.exchange_user_code`); the link flow still discards its token.
- Refresh under a row lock (`user_connections.fresh_user_token`), because GitHub's refresh token is single-use.
- **Act is its own page (`/act`), not a mode inside chat.** `POST /act` makes one model call (`actions/service.py::propose`); `POST /act/actions/{id}/confirm|reject`; `GET /act/status`; Admin › Actions (`/admin/actions`) with toggles and the log. Ask and Chart import nothing from `app/actions/`.
- Actions: `linear.create_issue`, `linear.comment`, `github.create_issue`, `github.comment`.
- Context: the same vector search, visibility predicate and 0.35 gate as Ask, org-wide only; a guard-flagged chunk is dropped whatever the guard's mode; each document's viewers are read again with the predicate.
- **Changes from the design above, decided while building:**
  - A target comes **only from the asker's own words** (`draft.require_named`), checked before any API call, then resolved with their token. Pickers and "the document I referred to" as a target are later work; until then nothing in a retrieved passage can choose a target.
  - **Every passage shown to the model counts as a source** for the exposure check, used or not. Stricter than "what the draft used", which only the model could claim, and an injected model would lie.
  - A **public Linear team**'s audience is every active member of the Linear workspace (`users`, ≤250, else unknown); a `restricted` team is unknown. A **private GitHub repo** is unknown; a public one is `public`, so private sources are refused there.
  - Linear writes send the `actions` row id as Linear's client `id`.
  - No conversation storage: the Act page lists the person's recent actions from `actions` itself.
- Tests: `tests/test_actions_act.py` (29), plus the Act prompt in `tests/test_untrusted_policy.py`.

**Phase 2: Notion + Google Docs on the web — BUILT (2026-10-14), live Notion test still to run**
- Actions: `notion.comment`, `notion.append` (text added at the end of a page), `notion.create_page` (under a page), `drive.create_doc` (a new Doc in the person's own Drive).
- Notion through the separate "Handbook Actions" connection (`NOTION_ACT_*`, `api/actions.py::_act_oauth`); the read connection is never used to write. A page is found by its link or an exact, unique title the asker wrote, among the pages they shared with Handbook Actions; a page Handbook indexed company-wide must be readable by the asker there; Notion's 403/404 says how to share the page. Texts are cut to Notion's 2,000 characters per text object and 100 blocks per call. Comments carry `display_name: user`; page and block writes say "via Handbook Actions, for you" on the card.
- Google: the same client, `drive.file` only, `include_granted_scopes=false` (so the token never picks up a broader scope the person once granted as an admin). The Doc is uploaded as a Google Doc in one multipart request. Its audience is the asker alone.
- **Every Act account must be the company's** (all four tools): the Linear or Notion workspace the company connected for reading, the person's own work address (or prior address) on Google, and for GitHub a repo in the company's installation. Otherwise company content could be written somewhere the company does not control. Linear now also requires the company's Linear connection to exist.
- The asker is never counted as an outsider of their own sources (they may read one through a group or an old address their email is not literally in).
- Tests: `tests/test_actions_notion_drive.py` (16).
- **Still to do before Notion page writes are promised:** the live test in §5 Notion (attribution, can-comment-only user).

**Phase 3: Slack — BUILT (2026-10-14)**
- **`slack.post_message`** (`actions/slack.py`): the company's bot token, never a person's (`token_for`); the text opens with "<@asker> asked Handbook to post this" and the draft is escaped like every bot reply, so a drafted `<!channel>` or `<url|text>` is inert. From a channel: only a reply in the same thread. Elsewhere: a channel the asker named, the bot is already in, and the asker belongs to (`conversations.members`, bounded; a cut list is "can't check"). A public channel is a public destination; a private one's audience is its members' emails (≤500, else unknown).
- **Act from Slack, in plain words** (`api/slack_act.py::slack_request`): `@Handbook create a Linear issue in Platform about this thread` in a channel, the same words in a DM. One small model check (`build_intent_prompt`) says whether the message ASKS for an action and quotes the words that do; the verdict is kept only when the quote is in the message. A question, an unreadable reply or a failed check is answered exactly as before. The check runs only where that Slack surface is switched on. `act <request>` still forces a draft without the check; with Act not allowed for the company, both are just questions. This replaced an explicit-`act`-only rule after looking at @Linear and @Jira (both plain words); safety rests on the approval, the quote check and the named target, not on a keyword. Every action works from Slack, not only Slack posting.
- **Each place is its own permission decision:**
  - DM: drafts from company-wide content as the PERSON (`viewer_for_person`); the card is a DM message.
  - Channel (public or private): drafts from THAT channel only, as the room (`Viewer(public_only=True, channels=(channel,))` + the channel tag), exactly like a channel answer, so the asker's private documents never reach a room. The card is ephemeral (only the asker sees it), falling back to the app DM.
  - Each surface has its own admin switch (`policy.SURFACES`, stored as `surface:slack_dm` / `surface:slack_channel` in `action_policies`), off by default and re-checked at approval. `actions.surface` records which.
  - Only the company's org-wide Slack connection can act; a space's Slack connection says so.
- **The click** (`POST /slack/interactivity`): signature-checked like `/slack/events`, acknowledged at once, decided in the background. The clicker's Slack account is resolved to a Handbook person and must be the proposer (`store.get` by org + user), the payload hash travels in the button, the tick travels in `state.values`, and the outcome replaces the card through `response_url`. A missing tick keeps the card.
- Slack users are found by their current or a prior work address (`users.get_member_by_address`), for answers too. A Slack request records the person's Slack account, so the web Act page can post to Slack for them.
- No new Slack scopes. Setup: Interactivity on, Request URL `https://<api-host>/slack/interactivity`.
- Tests: `tests/test_actions_slack.py` (28).

**Phase 4: later, each its own decision**
- Edit a Drive file chosen in Picker.
- Update a Linear issue's state or assignee (`destructive=True`: a second confirm, and the card shows a diff).
- Remembered approval per action per conversation for low-stakes actions (Dust "medium").
- Undo where the source supports it (Slack `chat.delete` of our own post, Linear archive).

---

## 7. What must hold (candidates for CLAUDE.md once built)

- A write runs only from an `actions` row the asker approved. The server executes the **stored** payload; the client's copy is only compared by hash.
- A write uses the asker's `user_connection`, never `oauth_connections`. The Slack bot is the one exception, and it names the asker and checks membership.
- A target is never taken from retrieved text alone.
- A link in written content is in the asker's own words or is a cited `source_uri`.
- Content from a private document never goes to a public destination. A destination wider than a private source's viewers, or of unknown audience, needs the person's stored acknowledgement.
- A timeout after sending is `unknown`, never retried automatically.
- `ActionOutcomeUnknown` and `failed` are shown to the person, never swallowed.
- No action runs unattended (no scheduler, digest or channel-triggered writes).
- A member who is removed loses every write token in the same transaction.

---

## 8. Rejected alternatives

- **Write with the org connection plus our own ACL check.** Fastest to build. But the write shows as the admin or bot, and any gap in our ACL becomes a write with the admin's rights. That is the confused deputy Onyx's shared `custom_headers` path has.
- **Slack user tokens (post as the person).** Marketplace "do not impersonate", extra review, and the token reaches every channel the person can.
- **Trusting MCP `readOnlyHint` or other self-declared tool safety.** The spec says not to, and Onyx Craft refuses to.
- **No confirmation for "small" writes in v1.** Every comparable product confirms interactive writes. Onyx's chat path, which doesn't, is the counter-example.
- **A separate audit table.** The `actions` row already holds who, what, the payload hash, the decision and the outcome. A second table would drift from it.

---

- **No admin log (decided 2026-10-14).** Admin › Actions holds the switches only. What a person drafted, its sources and drafts never sent are theirs (their Act page); each tool already records who created what. `actions` rows stay as the approval queue and each person's own history.

## 9. Open questions

1. Slack Marketplace listing: recommended (see §5 Slack: the API Terms and the new-install rate limit). Indexing stays allowed if the Agent/Assistant UI stays off. Pending.
2. Should workspace owners be able to **enable** an action the org left off for their space, or only disable? The plan says only disable.
3. ~~GitHub: one App or two?~~ Decided 2026-10-14: one App with Issues: write only, read-only installation tokens, and a narrowed token per write (§5 GitHub).
4. Retention: keep `actions` forever, or purge after N days like `live_tool_calls`? Payloads contain company text.

---

## 10. Sources

- Onyx source `onyx-dot-app/onyx@587a4a1`: `backend/onyx/tools/tool_runner.py`, `tools/tool_constructor.py`, `db/models.py` (`Tool`, `OAuthUserToken`, `MCPConnectionConfig`, `ToolCall`, `ActionApproval`), `sandbox_proxy/addons/gate.py`, `sandbox_proxy/request_evaluator.py`, `docs/craft/features/egress-proxy-and-approvals/README.md`; https://docs.onyx.app/admins/actions/mcp
- Glean: https://docs.glean.com/actions/actions-overview, https://docs.glean.com/administration/actions/managing-actions/run-without-user-confirmation
- M365 Copilot: https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/api-plugin-confirmation-prompts
- Atlassian Rovo: https://support.atlassian.com/rovo/docs/agent-actions
- ChatGPT developer mode: https://developers.openai.com/api/docs/guides/developer-mode
- Dust: `dust-tt/dust` `front/lib/actions/constants.ts`; https://docs.dust.tt/changelog/️-customizable-tool-approval-settings-for-mcp-tools
- MCP spec 2026-07-28 tool annotations: https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/schema/2026-07-28/schema.ts
- Linear: https://linear.app/developers/oauth-2-0-authentication, https://linear.app/developers/oauth-actor-authorization
- GitHub: https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens, https://docs.github.com/en/rest/authentication/permissions-required-for-github-apps
- Notion: https://developers.notion.com/guides/get-started/public-connections, https://developers.notion.com/reference/comment-display-name, https://developers.notion.com/reference/capabilities
- Google: https://developers.google.com/workspace/drive/api/guides/api-specific-auth, https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification
- Slack: https://docs.slack.dev/reference/methods/chat.postMessage, https://docs.slack.dev/interactivity/handling-user-interaction, https://docs.slack.dev/slack-marketplace/slack-marketplace-app-guidelines-and-requirements/
- Security: https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/, https://ai.meta.com/blog/practical-ai-agent-security/, https://genai.owasp.org/llmrisk/llm062025-excessive-agency/, CaMeL arXiv 2503.18813
