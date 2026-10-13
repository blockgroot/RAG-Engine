# Write Actions Plan

> **Status (2026-10-13): PROPOSED.** Nothing here is built. Research sources are
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
   | GitHub | GitHub App **user-to-server** token (the "link" flow, kept instead of discarded) | App gains Issues: write, Pull requests: write. Contents: write only if "open a PR" ever ships | The person, with the App badge. Can do only what both the person and the App can do |
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
   - env `ACTIONS_ENABLED` (kill switch)
   - org admin enables each action
   - a workspace owner may switch actions **off** for their space, never on beyond the org's setting

5. **Create and comment only in v1.** No edit-in-place of someone else's content, no delete, no close, no merge. Each later action is its own decision.

---

## 4. Design

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

Exceptions are `ActionError(ProviderError)`, with subclasses `ActionRejected`, `ActionExpired`, `ActionForbidden` and `ActionOutcomeUnknown`. Each takes `cause=` and uses `raise ... from`.

Config is a frozen dataclass `ActionsSettings.from_env()` in `settings.py`:
- `ACTIONS_ENABLED=false`
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
    status          TEXT NOT NULL,          -- proposed|approved|executing|succeeded|failed|unknown|rejected|expired
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

### 4.4 Flow, web chat

```
Ask ──► classify_route (existing question check)
          kind == "act"  (model reads the question; no word list)
          │
          ▼
        retrieval as today (same gate, same viewer)
          │
          ▼
        action agent: ONE generate_with_tools call over the
        actions enabled for this org/space AND connected by this person
          │  tool_calls[0]
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
  - Linear's `issueCreate` accepts a client-chosen `id` *(unverified)*. If so, pass `actions.id` and a retry becomes safe for Linear.
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
- Whether `actor=user` limits creation to the person's teams is *(unverified)*. We also check that the target team is one the asker sees (team membership is already the ACL for issues).

**GitHub.**
- Add Issues: write and Pull requests: write to the App. Every installation owner must **approve the new permissions** on GitHub. Until they do, reads keep working and writes return 403, which we surface as "Ask your GitHub admin to accept the new permissions."
- Store the user-to-server token from the link flow. It lasts 8 h with a 6-month refresh token; keep expiry on.
- Commenting on a PR uses the issues comments endpoint and needs Pull requests: write.
- "Open a PR" (Contents: write) is out of scope.

**Notion.**
- Per-user public OAuth. The person chooses pages in Notion's picker, so writes reach only pages they shared and can edit. This is the first place we get a real per-person Notion check: a page they cannot access returns `object_not_found`, which we show as "you can't edit that page".
- The org-wide read integration stays as it is. The Sources card wording about Notion permissions stays true for reads.
- Notion Personal Access Tokens (May 2026) also act as their creator. Do not use them; pasting a token is worse UX and lacks a refresh path.

**Google.**
- Separate per-user OAuth: `drive.file`, `access_type=offline`, `include_granted_scopes=true`.
- Check the granted scopes, because people can untick.
- v1 creates a Doc in the person's own Drive, optionally inside a folder they pick.
- Editing an existing file requires that the person picked it in Google Picker. That is Phase 4.

**Slack.** No new scopes. Membership checks use `conversations.members`, which works only for channels the bot is in; a channel the bot isn't in is refused. Do not request `chat:write.public` or `chat:write.customize`.

---

## 6. Phases

Each phase ships behind `ACTIONS_ENABLED` and the org toggle. Per our test practice, each phase runs **only its own new test file**.

**Phase 0: groundwork (no user-visible change)**
- Set `connected_by_user_id` in both `save_connection` calls.
- Add `user_connections`, `actions`, `action_policies`.
- `app/actions/` skeleton, `ActionsSettings`, exceptions.
- Fix the Slack user mapping so it reads aliases.
- Tests: `tests/test_actions_store.py`:
  - single execution under concurrent confirms
  - org and user isolation on every query
  - an expired proposal can never execute
  - hash mismatch is refused

**Phase 1: Linear + GitHub on web chat**
- Per-user connect flows (Linear new; GitHub stops discarding the token).
- `act` kind in `classify_route`; action agent; `prepare`/`execute` for `linear.create_issue`, `linear.add_comment`, `github.comment`, `github.create_issue`.
- `proposed_action` on `done`; `POST /actions/{id}/confirm|reject`; `ActionCard`; Admin › Actions toggles and log.
- Tests: `tests/test_actions_targets.py`, the injection suite:
  - a retrieved passage naming another repo or team, or containing "post this to #general", never becomes a target
  - links not in the asker's text are stripped
  - a non-member's handle does not resolve
  - a Viewer that can't see the source doc can't target it

**Phase 2: Notion + Google Docs (create) on web chat**
- `notion.add_comment`, `notion.append_to_page`, `notion.create_child_page`, `drive.create_doc`.

**Phase 3: Slack**
- `slack.post_message` (bot, membership-checked, names the asker).
- `/slack/interactivity`; ephemeral approval in Slack for every Phase 1–2 action.

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

## 9. Open questions

1. Is a Slack Marketplace listing planned? If so, our Slack *indexing* conflicts with the AI-app rule "DON'T store any Slack data" independently of writes. Worth deciding before Phase 3.
2. Should workspace owners be able to **enable** an action the org left off for their space, or only disable? The plan says only disable.
3. GitHub: are we comfortable asking every installation to approve new write permissions, or should writes be a second, opt-in GitHub App?
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
