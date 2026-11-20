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
  just not answering), never from a document outside the asker's visibility. Emails come from:
  an `email:` `source_editor_key` or an email in `source_meta.people`; a `slack:U…` key resolved
  with the existing cached `slack_utils._member_email`. At most 5 such lookups per suggest call.
  Drive sync additionally requests `lastModifyingUser.emailAddress` and stores it in
  `source_meta`, so newly synced Drive docs give an email. A document with no email gives no
  suggestion; that is acceptable, since the email box is always there. Only company-domain emails; never the asker themself. Each shows one reason:
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
  is the answer. `slack_events._handle` checks, before anything else, whether a DM message's
  `thread_ts` matches an open ask for that channel; if so it records the reply and stops
  (it is not treated as a question). Later messages in the thread are ignored in v1.
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
  OAuth scope — the company's own app and `.env`'s app both need it). A 403 → `ProviderError`
  "Handbook can't write to this page".
- **Drive:** add the `drive.file` scope to the Google connection (`app/config/sources.py`).
  Create a Google Doc with `files.create` (mimeType `application/vnd.google-apps.document`,
  `parents=[answers folder]`, uploaded as text). The folder is made by Handbook, so
  `drive.file` covers the doc. **Risk, checked first (task 1):** whether `drive.file` may create
  the answers folder *inside* the admin's synced folder, which Handbook did not create. If Google
  refuses, Drive is dropped from v1 (Notion only) and brought back to the user with options
  (the broader `drive` scope needs Google's verification). There is no "save in root" fallback:
  a doc outside the synced folder is never indexed, or is indexed for one account only.
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

## 6. Tests (each the smallest that fails if the rule breaks)

- Suggestions never come from a document outside the viewer's visibility.
- `POST /ask-friend` refuses another user's gap, a non-company email, and the limits.
- A DM thread reply on an open ask is recorded and is not answered as a question.
- Only the answering colleague can Save; Save calls the writer once with verbatim text.
- Notion/Drive writers build the right request (HTTP faked).
- Feedback topic status from asks; no names in the payload.
- Retention rule deletes closed asks after 90 days.

## 7. Left to the builder

- Exact UI styling and copy, within the sketches above.
- How suggestions are ranked among visible documents (closest documents first is enough).
- How the Slack user's display name is fetched for the DM greeting.
- Splitting the work into tasks and their order (the Drive-folder risk in 5.6 goes first).
