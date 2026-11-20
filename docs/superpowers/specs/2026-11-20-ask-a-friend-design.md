# Ask a friend — design

Date: 20 Nov 2026 · Branch: `feat/ask-a-friend` · Status: draft for approval

## 1. Why

When Handbook has no document for a question, the asker hits a dead end ("I couldn't find
this"). Today the refusal is logged as a gap and, after 3+ people, the admin sees a topic on the
bell — but the asker still leaves with nothing, and the knowledge stays in someone's head.

Ask a friend turns the dead end into a loop:

1. The asker gets an answer from a colleague instead of nothing.
2. The colleague answers once; with one click their answer becomes a real document in the
   company's own Notion or Drive.
3. Handbook indexes that document like any other, so the next person gets an instant, cited
   answer. The admin sees which gaps were answered and where the doc is.

## 2. Goals, non-goals, anti-goals

**Goals**
- From a refused web-chat answer, the asker can send the question to a colleague in two clicks.
- The colleague receives it as a Slack DM, replies in that DM thread, and the reply reaches the
  asker (in the chat, on the bell, and by email).
- The colleague can save the reply as a doc in the place the admin chose, in one click.
- The admin chooses, per scope (company or space), the connector (Notion or Drive) and the
  page/folder where answers are saved. The admin's Feedback page shows each gap topic's status.

**Non-goals (v1)**
- Asking from Slack (a refused Slack question). Web chat only.
- Saving answers to a Slack channel. The bot's own Slack posts are not indexed (CLAUDE.md §5),
  so a bot-posted answer would never be found again.
- "Not me, ask Arjun" (passing the ask on). The colleague can reply "I don't know".
- Grouping several askers of the same question into one ask to the colleague.
- Email as the channel to the colleague. Slack only; with no Slack connected the button is hidden.
- The AI rewriting the colleague's answer. The doc is their words, verbatim.

**Anti-goals (a failure even if it works)**
- A suggestion that reveals a private document: naming someone because they edited a doc the
  asker cannot open.
- Spam: a colleague flooded with asks, or a bot DM they cannot dismiss.
- A saved answer that looks like official policy with no source: every doc says who answered
  and when.
- Handbook writing anywhere except new docs inside the admin's chosen place.
- An ask that silently disappears: the asker always sees waiting / answered / "doesn't know" /
  expired.

## 3. Constraints (from CLAUDE.md and the code)

- `org_id` comes only from the session (`app/api/deps.py`); every table carries `org_id`; a
  space nests inside it (`workspace_id`, `NULL` = company-wide) and a non-member gets 403.
- Suggestions go through the one visibility predicate (`security/visibility.py`) with the
  asker's `Viewer`.
- A colleague must have the company email domain (`auth/users.assert_company_email`).
- New capability = new package (`base.py` + impl + `factory.py`) only where there are several
  implementations: the **doc writer** has two (Notion, Drive). The rest is an orchestrator
  (no `base.py`).
- Config through `app/config/` dataclasses; failures raise `ProviderError` subclasses.
- Rate limits: the ask endpoint takes `limit_request`; the asker email takes `limit_email`.
- A growing table gets a rule in `jobs/retention.py`.
- Outside text (the colleague's reply) shown to a model must use `UNTRUSTED_POLICY` — v1 shows
  it to no model; it reaches the model only later as an ordinary indexed document.
- **Changed rule (agreed 20 Nov):** CLAUDE.md says every write goes through an admin-added MCP
  server with the person's own account. Ask a friend is an exception: the company's sync
  connection creates new docs, because no AI chooses anything — the place is fixed by the
  admin, the text is the colleague's own reply which they see before saving, and the writer
  can only create inside that place. CLAUDE.md gets this as a written exception.

## 4. Approaches considered

| | How a saved answer is written | Trade-off |
|---|---|---|
| A | Agent mode: the colleague approves an Act card, written with their own MCP sign-in | Fits the old rule, but needs Agent mode on, the server added, the write tool enabled and a first sign-in — few answers would get saved |
| **B (picked)** | Handbook's own Notion/Drive connection, with added write permission | One click. Needs the CLAUDE.md exception and new connector permissions (fine: no company onboarded yet) |
| C | Store answers inside Handbook as its own source | Rejected by the user: answers live in the company's tools |

Picked **B** because the point is usefulness, and every setup step in A cuts the number of
answers saved. Reconnecting for new permissions costs nothing before launch.

## 5. The design

### 5.1 The asker (web chat)

Under a refused answer (`done.grounded == false`, and the refusal was recorded as a gap — same
condition as `chat.py:1659`), the chat shows a button **Ask a teammate**, only when the scope
has a live Slack connection.

Clicking opens a small panel:

```
Who might know?
  ○ Priya Shah — edits "Leave Policy"
  ○ Arjun Mehta — wrote in #hr about leave
  ○ Someone else: [ name@company.com ]
                                   [Send question]
```

- **Suggestions** (`GET /ask-friend/suggest?gap_id=`): up to 2 people, from the documents the
  asker can see that came closest to the question (below the gate is fine — they are visible,
  just not answering), never from a document outside the asker's visibility. Emails come only from
  what sync already stored in `source_meta.people` (Notion editor/creator, Drive last editor —
  `emailAddress` is already requested —, Linear assignee/creator/commenter, Slack authors), roles
  other than `mentioned`. No API call on the suggest path. On staging (20 Nov) 34 of 36
  documents carry such an email. A document with none gives no suggestion; the email box is
  always there. Only company-domain emails; never the asker themself. Each shows one reason:
  the document title, or the Slack channel name for a Slack thread. No suggestions → only the
  email box.
- **Send** (`POST /ask-friend`): body `{gap_id, colleague_email}`. Server checks: the gap row
  belongs to this user and org; the email is company-domain; the email is a member of the
  connected Slack team (`users.lookupByEmail`); limits (below). The question text is taken from
  the gap row, never from the client.
- The refused turn then shows the ask's status, read with the chat history:
  `Asked Priya · waiting` → `Priya replied: "…"` / `Priya doesn't know` / `No reply in 7 days`.

`record_gap` returns the new row's id; the `done` event carries it as `gap_id` (only when a gap
was recorded). That id is how the ask links to the question, the conversation and, later, the
gap topic.

### 5.2 The colleague (Slack DM)

The bot opens a DM (`conversations.open`, scope `im:write`, already granted) and posts:

```
Rahul Verma asked Handbook a question it couldn't answer, and thought you might know:

  "What's the notice period for interns?"

Reply in this thread and Rahul will get your answer.
[I don't know]
```

- **Reply:** the first non-bot message in that DM thread (`thread_ts` = the ask's message ts)
  is the answer. `slack_events._handle` checks right after `_org_for_team` — **before** the
  sender is resolved to a Handbook user, because the colleague may have no account (today that
  path posts "no account" and stops) — whether a DM message's `thread_ts` matches an ask of that
  org on that channel **and** the sender is the ask's `slack_user_id`. If so it records the reply
  and stops (not a question; no rate-limit bucket, no model). Anything else falls through to the
  normal path unchanged. Later messages in the thread are ignored in v1.
- **I don't know:** a button (`action_id = friend_dont_know`) closes the ask as declined.
- After a reply, the bot posts in the thread:

```
Thanks — sent to Rahul.
Save this as a doc so others can find it? It will be saved in Notion › Team Answers,
visible to everyone who can see that page.
[Save as doc]  [Don't save]
```

  The buttons appear only if the scope has a save destination configured; otherwise
  "Thanks — sent to Rahul." only.

### 5.3 Telling the asker

On reply or decline the asker gets:
- the status on the refused turn in their chat (read from `friend_asks` when the conversation
  loads);
- a bell item (`notifications._ask_items`, computed like the others: answered/declined asks the
  asker has not opened yet, "Priya answered your question", linking to the conversation);
- an email (`send_friend_answer_email_safe`, `limit_email`), with the answer text and a link.

Opening the conversation marks the ask seen (`seen_at`), which clears the bell item.

### 5.4 Saving as a doc

`Save as doc` (`action_id = friend_save`) — only the colleague who answered may click it
(Slack user id checked against the ask). Handbook:

1. Builds the doc with no AI:
   - title: the question (trimmed to 120 chars);
   - body: the reply verbatim, then `Answered by Priya Shah, 20 Nov 2026, via Handbook.`
2. Calls the scope's doc writer (`app/answerdocs/`, see 5.6) to create it in the configured
   place.
3. Requests a sync of that connection (`autosync.request_sync`) so it is indexed soon.
4. Updates the Slack message: `Saved: <link>`; stores `doc_url` on the ask.

On failure: the message says "Couldn't save — ask your admin to check Ask a friend settings",
the ask keeps `save_error`, and nothing retries.

### 5.5 Admin settings

New section on the admin connections area (and the same for a space's owner):

```
Ask a friend
  Save answers in:   (•) Notion   ( ) Google Drive   ( ) Don't save
  Notion page:       [ Team Answers ▾ ]   (pages shared with Handbook)
  Drive folder name: [ Team Answers ]     (Handbook creates it inside your synced folder)
```

- Stored on the destination connection's `oauth_connections.source_config`:
  `answers_parent_id` (Notion page id or Drive folder id) and `answers_label`. Only one
  destination per scope: setting one clears the other.
- **Notion:** the picker lists pages from the connection (already shared with Handbook).
  A child page of a shared page is shared too, so it is synced with no extra step.
- **Drive:** the admin types a name; Handbook creates the folder inside the connection's synced
  folder (`source_config.folder_id`, always set: the Drive adapter requires it) and stores its
  id. Inside the synced folder means the next sync finds the doc, and the doc inherits that
  folder's sharing, so it is indexed for the same people who see the rest of the folder.
- API: `GET/PUT /admin/ask-friend` and `/workspaces/{id}/ask-friend` (owner only, 403
  otherwise).

### 5.5a Which scope an ask uses

An ask takes the scope of the chat it came from (`conversations.workspace_id`).
- **Slack (transport):** the space's Slack connection if it has one, else the company's. The DM
  carries only the asker's own question, so the company bot may deliver it.
- **Save destination:** only that scope's own setting. A question asked in a space is never
  saved into a company-wide page (it could expose the space's subject to the whole company);
  a space with no destination shows no Save buttons.
- The colleague need not be a space member: the asker chose to share their own question.

### 5.6 Doc writers (`app/answerdocs/`)

New package: `base.py` (`AnswerDocWriter.create(parent_id, title, body) -> url`), `notion.py`,
`drive.py`, `factory.py` (`build_answer_doc_writer(provider, token)`), `__init__.py`.

- **Notion:** `POST /v1/pages` with `parent.page_id`, a title and paragraph blocks. Needs the
  Notion integration's **Insert content** capability (set in the integration's settings, not an
  OAuth scope — every integration a connection may use needs it: the OAuth app and the per-org
  `NOTION_TOKEN_<NAME>` internal integrations). A 403 → `ProviderError`
  "Handbook can't write to this page".
- **Drive:** add the `drive.file` scope to the Google connection (`app/config/sources.py`) as an
  **optional** scope, like Forms and Groups: Google lets a person untick it, and CLAUDE.md says a
  missing optional scope must not fail the Drive connect (`google_oauth._verify_granted_scopes`
  today refuses any missing scope, so it must skip this one). Without `drive.file` the admin
  settings say "Reconnect Google Drive to allow saving" and no Save buttons are shown.
  Create a Google Doc with `files.create` (mimeType `application/vnd.google-apps.document`,
  `parents=[answers folder]`, uploaded as text). The folder is made by Handbook, so
  `drive.file` covers the doc. **Risk, checked first (task 1):** `drive.file` only reaches files
  Handbook created (or a person opened with it), so creating the answers folder *inside* the
  admin's synced folder will likely be refused. Order of attempts:
  1. Create it inside the synced folder directly (works if Google allows it).
  2. Otherwise Handbook creates "Team Answers" in the connected account's My Drive and the
     settings page says "Move this folder into <synced folder> in Google Drive". Handbook made
     it, so it keeps access after the move; Save stays off until Handbook sees (one
     `files.get` of its `parents`) that it sits inside the synced folder. Once moved it inherits
     the folder's sharing and is synced.
  No doc is ever saved outside the synced folder: it would never be indexed, or only for one
  account. The broader `drive` scope (Google verification) is not used.
- The writer only creates. It has no update or delete.

Token: `get_live_connection_token(org_id, provider, workspace_id)` (refreshes Google).

### 5.7 Admin Feedback page

Each gap topic gets a status from the asks linked through `feedback_and_gaps.topic_id`
(newest ask wins):

| Status | Shown |
|---|---|
| Doc saved | ✅ Answered — doc saved (link) |
| Answered, not saved | 🔒 Answered privately |
| Waiting | 💬 Asked a colleague |
| Declined / expired / none | ⚪ Not answered |

A topic with a saved doc is marked handled (`handled_at`) automatically. The admin never sees
who asked, who was asked, or a private reply's text (counts only, as today).

### 5.8 Data

```sql
CREATE TABLE friend_asks (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  workspace_id  UUID REFERENCES workspaces(id) ON DELETE CASCADE,
  gap_id        UUID REFERENCES feedback_and_gaps(id) ON DELETE SET NULL,
  conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE,
  question      TEXT NOT NULL,       -- copied from the gap row at send time
  asker_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  colleague_email TEXT NOT NULL,
  colleague_name  TEXT,
  slack_user_id TEXT NOT NULL,
  slack_channel TEXT NOT NULL,       -- the DM channel
  slack_ts      TEXT NOT NULL,       -- the ask message; replies are threaded on it
  status        TEXT NOT NULL DEFAULT 'waiting'
                CHECK (status IN ('waiting','answered','declined')),
  answer        TEXT,
  answered_at   TIMESTAMPTZ,
  seen_at       TIMESTAMPTZ,
  doc_url       TEXT,
  save_error    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- lookups: (org_id, slack_channel, slack_ts); (asker_id, status); (gap_id)
```

"Expired" is computed (`waiting` and older than 7 days), not stored. A reply after expiry is
still accepted. The answer text is the asker's own data, like their chat. Retention: an ask is
deleted 90 days after it closed (or after expiry), via `jobs/retention.py`. Gap rows are
deleted 90 days after creation by the existing rule; `gap_id` then becomes NULL and the ask
keeps its own copy of the question and conversation, so the asker's chat still shows it.
Topic status on the Feedback page only covers the topic window, which is shorter.

### 5.9 Limits

- An asker: at most 5 waiting asks at once; one ask per gap.
- A colleague: at most 5 waiting asks from anyone. Over the limit → "Priya has several open
  questions already — try someone else."
- Constants in an `AskFriendSettings` dataclass (`app/config/`), `from_env()`.

### 5.10 CLAUDE.md and docs

- §5 "Act (writes)": add the exception — Ask a friend's doc writer creates (never edits)
  docs with the company's sync connection, only inside the admin's chosen place, only the
  colleague's verbatim reply, only on that colleague's click.
- §3: suggestions only from documents the asker can see; Slack messages to colleagues never
  include anything but the question and the asker's name.
- `docs/handbook/PRODUCT_STATUS.md`: a row for Ask a friend.

## 5.11 Constraint check (CLAUDE.md, rule by rule)

| Rule | How Ask a friend meets it |
|---|---|
| `org_id` only from the session | `POST /ask-friend` and settings take it from `deps.py`; the question comes from the gap row, never the client |
| `org_id` on every tenant table, filtered in every query | `friend_asks.org_id`; every read filters on it; Slack lookups match the ask's org to `_org_for_team` |
| Workspace nests in org; non-member 403 | Space settings via `assert_member` + owner check; space questions only save to the space's place (5.5a) |
| Company email domain | Colleague email checked with `assert_company_email` |
| One visibility predicate | Suggestions read only documents passing it with the asker's `Viewer`; a public-only viewer gets none from private docs |
| Capture fails closed (Drive) | The doc sits in the synced folder and inherits its sharing; the normal sync captures it. No root fallback |
| Notion has no per-page permissions | The Save prompt says the doc is visible to everyone who can see that page |
| A conversation is personal; no answer cache | The reply is shown only to the asker. A saved doc is a real document under normal access checks, not a cache |
| Bot's own Slack traffic not indexed | Asks and replies live in DMs, which sync never reads; the Slack-channel destination is out of v1 for this reason |
| Slack request acted on only for a connected team | Buttons go through the existing `_interact` (team checked); replies matched on org + channel + ts + sender |
| Every write via MCP (Act) | **Exception added to CLAUDE.md** (§3, §5.10). `app/answerdocs/` imports nothing from `app/actions/` and vice versa |
| Failures are `ProviderError` | Writers raise `ProviderError` subclasses with `cause=` |
| Config only in `app/config/` | `AskFriendSettings.from_env()`, read through `settings.py` |
| New capability = package with `base.py` | `app/answerdocs/` (two writers); the ask flow is an orchestrator without `base.py` |
| Bound external walks, mark truncation | Suggestions: ≤ 10 documents, no API calls; the Notion page picker is bounded and says when it is cut |
| Untrusted text in prompts | No prompt in v1 sees the reply; once saved it is an ordinary document under the normal policy |
| Links only if seen verbatim | No model writes text here; the doc URL comes from the provider's API reply |
| Rate limits | Routes take `limit_request`; the asker email takes `limit_email`; no AI call, so no AI spend |
| Optional scope must not fail connect | `drive.file` is optional (§5.6) |
| Growing table → retention rule | `friend_asks` rule in `jobs/retention.py`, bounded per pass |
| Schema conventions | `CREATE TABLE friend_asks` in `schema.sql`; `ALTER`s follow their table; no bare data fix |
| Identifier masking (`app/privacy`) | Covers LLM, search and guard. The question goes to the company's own Slack and the reply to its own Notion/Drive, like the bot's replies today: not masked (masking would corrupt the saved doc). Said in the CLAUDE.md exception |
| Chart refusals never shown as gaps | The button appears only where a gap is recorded (same condition as `chat.py:1659`), so chart and access refusals never get it |
| Heavy imports inside functions | Writers import HTTP clients inside methods |

## 6. Tests (each the smallest that fails if the rule breaks)

- Suggestions never come from a document outside the viewer's visibility.
- `POST /ask-friend` refuses another user's gap, a non-company email, and the limits.
- A DM thread reply on an open ask is recorded and is not answered as a question.
- Only the answering colleague can Save; Save calls the writer once with verbatim text.
- A DM reply from someone with no Handbook account is still recorded; any other DM still takes
  the normal path.
- A Google connect without `drive.file` still succeeds.
- Notion/Drive writers build the right request (HTTP faked).
- Feedback topic status from asks; no names in the payload.
- Retention rule deletes closed asks after 90 days.

## 7. Left to the builder

- Exact UI styling and copy, within the sketches above.
- How suggestions are ranked among visible documents (closest documents first is enough).
- How the Slack user's display name is fetched for the DM greeting.
- Splitting the work into tasks and their order (the Drive-folder risk in 5.6 goes first).
