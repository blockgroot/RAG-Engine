# Handbook: Current Features and Status

*As of 13 November 2026 · code at* `main`

Handbook is an AI assistant that answers employees' questions from their company's own tools:
Notion, Google Drive, Slack, Linear and GitHub. Every answer names its source. When the answer
isn't in the company's documents, Handbook says so instead of guessing. People only see what each
tool already lets them see.

## Summary


| #   | Feature                                           | Status                                                                                                                    | Main code                                                  |
| --- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| 1   | Ask: grounded answers                             | Live                                                                                                                      | `app/rag/`, `app/api/chat.py`                              |
| 1b  | Citations on an answer                            | Live                                                                                                                      | `app/rag/cite.py`, `frontend/components/AnswerText.tsx`    |
| 2   | Smart routing between tools                       | Live                                                                                                                      | `app/agent/routing.py`                                     |
| 3   | Connectors (Notion, Drive, Slack, Linear, GitHub) | Live                                                                                                                      | `app/sources/`, `app/githublive/`                          |
| 4   | Personal Spaces                                   | Live                                                                                                                      | `app/workspaces/`                                          |
| 5   | Access control per document                       | Live (Drive, Slack, GitHub); Live on staging (Linear public teams, charts); Built, not tested live (Linear private teams) | `app/security/visibility.py`, `app/githublive/access.py`   |
| 6   | Second Brain: knowledge graph                     | Live                                                                                                                      | `app/graph/`                                               |
| 7   | Second Brain: live tool reads                     | Live                                                                                                                      | `app/livetools/`                                           |
| 8   | Second Brain: personal memory                     | Live                                                                                                                      | `app/memory/personal.py`                                   |
| 9   | Charts from tool activity                         | Live                                                                                                                      | `app/insights/`, `frontend/components/charts/`             |
| 10  | Charts from documents and uploaded files          | Live                                                                                                                      | `app/doctables/`, `app/insights/tables.py`                 |
| 11  | Ask in Slack                                      | Live                                                                                                                      | `app/api/slack_events.py`                                  |
| 12  | Scheduled reports                                 | Live                                                                                                                      | `app/schedulers/`                                          |
| 13  | File uploads                                      | Live                                                                                                                      | `app/attachments/`                                         |
| 14  | Model choice and a company's own model            | Live                                                                                                                      | `app/llm/`                                                 |
| 15  | Feedback and documentation gaps                   | Live                                                                                                                      | `app/feedback/`                                            |
| 16  | Notification bell                                 | Live                                                                                                                      | `app/api/notifications.py`                                 |
| 17  | Automatic sync (hourly)                           | Live                                                                                                                      | `app/jobs/autosync.py`                                     |
| 18  | Instant updates (webhooks)                        | Live                                                                                                                      | `app/api/webhooks.py`, `app/sources/drive_watch.py`        |
| 19  | Prompt-injection protection                       | Live                                                                                                                      | `app/security/`, `app/guard/`                              |
| 20  | Sign-in, email delivery and email change          | Live                                                                                                                      | `app/auth/`                                                |
| 21  | Chat history (rename, pin)                        | Live                                                                                                                      | `frontend/components/RailChats.tsx`                        |
| 22  | Google Groups access                              | Built, switched off                                                                                                       | `app/sources/google_groups.py`                             |
| 23  | Google Forms sentiment                            | Built, not tested live                                                                                                    | `app/sources/google_forms.py`, `app/insights/sentiment.py` |
| 24  | Answer fact-checker                               | Built, switched off                                                                                                       | `app/rag/audit.py`                                         |
| 25  | Agent mode (writes into tools)                    | Built, on `main`; off until a company's admin turns it on                                                                 | `app/actions/`, `app/mcphost/`                             |
| 26  | Each company's own Google and Slack app           | Built, on `main`                                                                                                          | `app/auth/org_apps.py`                                     |
| 27  | Usage limits (requests, answers at once, AI)      | Built, on `main`                                                                                                          | `app/security/rate_limit.py`, `app/security/ai_spend.py`   |
| 28  | Personal identifiers masked                       | Built, on `main`                                                                                                          | `app/privacy/`                                             |
| 29  | Answers worked out from sheet rows                | Row lookup on; worked figures built, switched off                                                                         | `app/doctables/store.py`, `app/insights/table_answers.py`  |
| 30  | Self-hosted install and Updates                   | Released (0.2.0 to 0.4.1)                                                                                                 | `deploy/selfhost/`, `app/updates/`                         |
| 31  | Release notes and the new-version pill            | Built, on `main`                                                                                                          | `release-notes/`, `frontend/components/NewVersionPill.tsx` |


---

## 1. Ask: grounded answers

**Status: Live**

One question box covers every connected tool. Handbook finds the most relevant passages using
meaning-based and keyword search together, re-ranks the best 40, and answers only from those
passages. Each answer shows the document, the app, who last edited it and when.

Two safety layers prevent invented answers:

1. If nothing is close enough, Handbook refuses without calling the AI.
2. If passages are found but don't actually answer the question, the prompt makes the AI refuse.

Ask also handles:

- **Follow-ups.** "What about part-timers?" is rewritten into a full question first.
- **Summaries.** "Summarise everything discussed here" reads the whole channel or space.
- **Deep analysis.** For questions that span several documents, Deep reads the top documents
in full instead of short passages. **Extra effort**, a setting under Deep, also reads widely
across many documents for list and company-wide questions.
- **Progress while it works.** A timeline shows which tools were searched and which documents
were found while the answer is written.
- **Web search.** One web search is allowed for outside topics, labelled as web. Only words the
user typed are sent.
- **Access refusals.** If the document exists but isn't shared with the asker, the answer says so
and names the tool, never the document.
- **Citations.** One document is named once under the answer, with a link that opens it in its
own tool. Two or more documents keep a small number on each sentence and the same list
underneath. The link is the address saved when the page was indexed. A number the model invents
is dropped. Slack shows the answer without the numbers. Opening an old chat shows the same
list, because it is stored on the turn.

**Limitations:**

- Very broad questions asked company-wide use the top passages, not every document.
- Summaries are capped at 120 passages or 60,000 characters, oldest dropped first.

**Code:** `app/rag/pipeline.py`, `app/rag/cite.py`, `frontend/components/AnswerText.tsx`, `app/rag/retrieval.py`, `app/rag/access_notice.py`, `app/api/chat.py`

## 2. Smart routing between tools

**Status: Live**

The person doesn't pick a tool. Handbook compares the question with each tool's content and picks
the closest match. Naming a tool ("in Slack…") or a repository picks it directly. Chart
questions go to the charts engine, and code questions go to GitHub. A wrong pick costs a refusal,
never a wrong answer, because the chosen tool still runs every safety check.

**Code:** `app/agent/routing.py`, `app/agent/orchestration.py`

## 3. Connectors

**Status: Live**

- **Notion.** Reads pages shared with the integration, including nested content.
- **Google Drive.** Reads Docs, PDFs, Word files, Google Sheets, Excel and CSV files in one chosen
folder. A spreadsheet is searched by a description of its columns; its rows are kept as a table
(see §10 and §29).
- **Slack.** Reads threads in channels an admin picks. Messages under 15 characters are skipped.
- **Linear.** Reads issues with status, assignee, team, priority, labels and comments.
- **GitHub.** Nothing is stored. Commits, pull requests, reviews, branches and READMEs are read
live through six tools.

Disconnecting a tool deletes everything indexed from it. Each connector shows how far a running
sync has got ("12 of 40 issues done"). Pressing Connect for a tool that isn't set up yet opens a
setup guide instead of an error.

**Code:** `app/sources/` (notion, google_drive, slack, linear), `app/githublive/`

## 4. Personal Spaces

**Status: Live**

A company can create private spaces, such as "Meeting notes", with their own members and tools. A
space sees only its own content, never the company-wide content. Members land on Ask, and owners
manage invites and connections.

**Code:** `app/workspaces/store.py`

## 5. Access control per document

**Status: per tool, as listed below**

Each document stores who may read it, taken from the tool at sync time. Every search adds the
rule "public in this space, or shared with this person". The rule runs inside the same database
query, before ranking. The same rule applies to charts, starter suggestions, reports, live reads
and the knowledge graph.

- **Google Drive: Live:** Access follows each file's sharing. If sharing can't be read, only the connected account sees the file.
- **Slack: Live:** Public channels are open to the space. Private channels are open to members only. A reply posted in a channel uses only that channel's content.
- **Linear: public teams verified on staging, 1 Oct.** Private teams are limited to their members,
admins and people an issue was shared with. This is built but not tested live, because private
teams need Linear's Business plan.
- **GitHub: Live (verified in production, 1 Oct).** A private repository answers only people whose linked GitHub account can open it. Unlinked members see public repositories only.
- **Charts: Live :** A chart counts only documents the viewer can open.
- **Notion: blocked, per space only.** Notion's API exposes no page sharing.

**Code:** `app/security/visibility.py`, `app/githublive/access.py`, `app/insights/store.py`

## 6. Second Brain: knowledge graph

**Status: Live**

The knowledge graph is a map of who worked on what, and which pages, issues, pull requests and channels relate. It lets one answer span tools, for example "what is Bob working on across Linear and Slack?"

- **Built from data we already have.** The graph uses what each sync already saw, so it makes no
extra calls to the tools.
- **People linked on proof only.** People are matched across tools by sign-in email or a linked
GitHub account, never by name.
- **Access checked at every step.** Every step through the graph checks the asker's access.

**Code:** `app/graph/` (identities, builder, linking, walk, plan)

## 7. Second Brain: live tool reads

**Status: Live**

When a question is about current state ("is SYV-5 still blocked?"), Handbook re-reads the item
from the tool instead of trusting the last sync. It reads at most 2 items, within 6 seconds. A
deleted or unshared item is withheld rather than shown out of date.

The Linear, Drive and Notion readers are on. The Slack reader is kept off because of Slack's rate
limits.

**Code:** `app/livetools/` (gateway, trigger, readers)

## 8. Second Brain: personal memory

**Status: Live**

Handbook remembers a few facts a person states about themselves, such as "I'm in the Bangalore
office" or "keep it short". It shows "Remembered · Undo" under the answer and uses these facts
only to interpret later questions, never as evidence. Sensitive topics are never stored. People
manage their facts on the Account page, and admins can turn memory off for the company. Memory
works in web chat only.

**Code:** `app/memory/personal.py`

## 9. Charts from tool activity

**Status: Live**

Turn on **Chart mode** beside the message box and ask, for example "commits per week by author"
or "Linear issues by state". The AI only fills in a request (what to count, how to split it, which
period, which shape); code checks it and SQL counts the stored rows. **The AI never produces a
number.** If the request can't be checked, the chart is refused with the reason, never guessed.

- **What can be charted:** 12 activity measures (Notion and Drive changes, GitHub commits, pull
  requests and reviews, Linear issues and cycle time, Slack conversations, Forms sentiment), plus
  any simple field a tool returns (labels, priority, state), found per space, not from a fixed list.
- **Shapes:** bar, horizontal bar, stacked bar, line, area, stacked area, pie, donut and heatmap,
  depending on whether there is a time axis and how many splits are asked for.
- **On screen:** drawn with ECharts. Hover shows the items behind a bar; each chart has a table
  view, PNG and CSV download, and can be pinned. The title names the measure and unit, and a line
  under it says what each bar counts.
- **Access:** a chart counts only documents the asker can open. GitHub is read fresh when a chart
  asks for it.

**Limitation:** GitHub charts are for the whole space, not filtered to the repos one person can open.

**Code:** `app/insights/` (registry, query, resolve, store), `app/agent/insights_agent.py`,
`frontend/components/charts/`

## 10. Charts from documents and uploaded files

**Status: Live**

The same Chart mode charts figures that sit inside documents.

- **Tables in documents.** Tables in Notion pages, Notion inline databases, Google Docs, Sheets and
  Word files are saved as rows when the page syncs (`doc_tables`, `doc_table_rows`), with no AI.
  A workbook is read tab by tab; tabs with the same columns are one table, so a total spans them.
  Two tables can be compared in one chart ("target vs achieved").
  A chart picks the table that best matches the question; the table's first column is its axis,
  rows keep the document's order, and percentages are averaged, never added up.
- **Figures written in paragraphs.** When a page with at least three figures syncs, the AI reads
  the whole page section by section and lists each figure with its measure, unit, period and label.
  Code builds the tables from that list and keeps a figure only when its sentence is in the page
  and the number matches its label. Nothing is hard-coded to particular words, units or
  languages. A page whose read fails is retried by later syncs and shown to admins on the
  Feedback page.
- **When nothing is saved.** Chart mode reads the page the asker named, or the closest one or two
  pages, live before saying there is no table.
- **Uploaded files.** Excel sheets, CSV, Word tables and PDF tables are read when the file is
  attached, with no AI. Figures in a file's sentences are read once, in Chart mode. Only the
  uploader, in that chat, can chart the file; removing it removes its tables.

Updates are automatic: a webhook or the hourly sync re-reads an edited page. A document table is
exactly as visible as its document.

**Code:** `app/doctables/` (table and text adapters, store, uploads), `app/insights/tables.py`

## 11. Ask in Slack

**Status: Live**

@mention the bot in a channel or send it a direct message, and you get the same answers as in the
app.

- **Channel replies.** Answers go in a thread and use only that channel's content, so private
documents never reach a shared room.
- **Direct messages.** Answers use everything the person can see and name the source and space.
- **While searching.** The bot shows "Searching…" and edits it into the answer.
- **Agent mode.** A message that asks for an action ("agent, post this to #team") gets an
approval card in Slack (see §25).

- **Charts.** A chart question gets a PNG image of the chart (Slack can't run interactive
charts), with an **Open interactive chart** link that asks the same question in the web app, under
the clicker's own access. A DM charts what the person can open; a channel charts only that
channel, and its link opens the channel's space. Needs the `files:write` scope; without it the
numbers are written as text.

Unanswered questions are logged here too, but Slack has no thumbs.

**Code:** `app/api/slack_events.py`, `app/chartrender/`

## 12. Scheduled reports

**Status: Live (email delivery verified 30 Sep)**

A member describes what they want in plain words and picks a tool, a space and a schedule: daily,
weekly or monthly. Each report is saved in the app and announced by email with a link. The first
report covers the last 90 days. Reports say what was checked, and they describe current content
rather than changes. The list keeps each schedule's latest 12 reports and says how many older ones
it leaves out.

**Code:** `app/schedulers/`

## 13. File uploads

**Status: Live**

Attach PDF, Word, Excel (.xlsx), CSV, TSV, text, Markdown or JSON files to a chat. Files are stored privately in
Cloudinary (or, with no Cloudinary keys, their text is kept in the database) and answered together with company documents, so "is this bill claimable?" reads the
bill and the expense policy. Each file gets its own result: one bad file doesn't block the
others. In Chart mode a file's tables and figures can be charted (see §10).

**Limitations:** no images, and no reusable file library.

**Code:** `app/attachments/`, `app/api/attachments.py`

## 14. Model choice and a company's own model

**Status: Live**

Members pick which AI model answers: Claude Sonnet 5.5 (strongest answers) or Claude Haiku 5.5
(fast and low cost). Both build charts. Admins can also add the company's own model from a list
of provider presets; its key is tested before it is saved and stored encrypted. Background work
(context lines at sync, figures in paragraphs, grouping questions) runs on a separate model that
nobody picks.

**Code:** `app/llm/catalog.py`, `app/llm/routed.py`, `app/llm/org_model.py`, `frontend/components/ModelPicker.tsx`

## 15. Feedback and documentation gaps

**Status: Live**

Every web answer has thumbs up or down. A thumbs-down asks for a reason: incorrect, out of date,
or unhelpful. Every unanswered question is recorded automatically, from both web and Slack, and grouped
with others that ask the same thing in different words. When 3 different people can't get an
answer on one topic in 14 days, or 2 mark its answers wrong, the bell tells whoever can fix it:
the admin for company-wide questions, the space's owner for questions asked in that space.
The alert opens the topic: how people asked it, whether anything came close, and the answer
marked wrong. "Mark as handled" hides it until it is asked again. Who asked is never shown.
Charts Handbook couldn't draw are not listed here; they are for us to improve.

Grouping uses the background model to decide whether two questions ask for the same thing;
embeddings only narrow which topics it compares against.

**Limitation:** grouping has been checked on a made-up set of questions, not yet on real ones.

**Code:** `app/feedback/`

## 16. Notification bell

**Status: Live**

The bell shows what someone can fix, only to the person who can fix it: admins for company-wide
tools, owners for their own space.

- Tools that stopped syncing or still need setting up.
- Topics people couldn't get answered (see §15).
- A document the safety model flagged, when the guard is enforcing: the item opens the page
itself, so its owner can remove the text.
- On a self-hosted copy, admins see a new release or what the last update changed.

An item can be dismissed, or all cleared at once. A dismissal is kept in that browser until the
problem changes.

**Code:** `app/api/notifications.py`, `frontend/components/NotificationBell.tsx`

## 17. Automatic sync (hourly)

**Status: Live**

Every tool is re-checked every hour. On Cloud an external timer calls Handbook every 10 minutes,
which also keeps the hosted server from going to sleep. On a self-hosted install the worker runs
these jobs itself. Only changed documents are re-processed. A new connection syncs right away.

**Code:** `app/jobs/autosync.py`, `.github/workflows/tick.yml`

## 18. Instant updates (webhooks)

**Status: Live (set up in production, 1 Oct)**

Slack, Linear, Notion and Drive notify Handbook when something changes, and a sync starts right
away. A burst of changes causes at most one sync every 3 minutes, and the next one starts by
itself when that window ends.

These timings were measured on staging:

- **Slack:** the sync starts in under 1 second.
- **Linear:** the sync starts in 0.7 seconds.
- **Notion:** new content is searchable in about 30 seconds.
- **Drive:** new content is searchable in about 3 minutes.

Production is fully set up: the webhook secrets, the Drive push address, Slack's channel-message
events, and the Linear and Notion webhooks pointing at production.

**Note:** each company has to reconnect Linear once to receive Linear's instant updates. Linear
only sends updates to workspaces that connect after the webhook is set up. Until a company
reconnects, the hourly re-check still picks up its Linear changes.

**Code:** `app/api/webhooks.py`, `app/sources/drive_watch.py`, `app/jobs/autosync.py`

## 19. Prompt-injection protection

**Status: Live**

Documents can contain hidden instructions aimed at the AI, so Handbook assumes the AI can be
fooled and makes that harmless:

- **One written rule.** The same rule goes around every block of outside text: document text is
material, not instructions.
- **Cleaning.** Hidden characters and fake "system" markers are removed before the AI sees the
text.
- **Links.** An answer may only contain links that were in the source text. Slack link previews
are off.
- **Canary.** A hidden canary catches the AI repeating its instructions.
- **Safety model.** A safety model screens documents and answers. Flagged documents are reported
to their owner. The guard can run on its own AI account (`GUARD_API_KEY`, `GUARD_BASE_URL`,
`GUARD_SAFEGUARD_MODEL`), so it never uses the quota of the models that answer.

**Limitation:** files over about 15,600 characters are not screened by the safety model.

**Code:** `app/security/untrusted.py`, `app/security/links.py`, `app/security/agents.md`, `app/guard/`

## 20. Sign-in, email delivery and email change

**Status: Live (email change verified on production)**

Sign-in uses single-use email links, with no passwords, sent as an email with a button. New
companies go through an approval queue (a self-hosted install creates its one company during
installation instead). Emails are sent through SMTP, Resend or SendGrid. People can change their
sign-in email after confirming the new address. The old address is kept, so files shared to it
still match.

A company's email domain is its first admin's. Inviting someone, or changing a sign-in email, to
any other domain (a personal address, or a subdomain) is refused.

**Code:** `app/auth/magic_link.py`, `app/auth/email_change.py`, `app/auth/email.py`

## 21. Chat history

**Status: Live**

Recent chats appear in the left rail. Each chat has its own link, can be renamed, pinned to the
top or deleted from its ⋯ menu, and is private to its owner.

**Limitation:** there are no automated browser tests.

**Code:** `frontend/components/RailChats.tsx`, `frontend/lib/chatsCache.ts`

## 22. Google Groups access

**Status: Built, switched off**

When a Drive file is shared with a Google Group, including nested groups, its members would be
able to get answers from it. This needs a Google Workspace admin to connect Google. With a
non-admin account the Google connection fails, so the feature stays off.

**Code:** `app/sources/google_groups.py` (flag `GOOGLE_GROUPS_ENABLED`)

## 23. Google Forms sentiment

**Status: Built, not tested live**

Handbook charts the sentiment of chosen surveys. Responses are never searchable, results are
shown to owners only, and groups under 5 responses are hidden. It has never run on a real form,
and switching it on makes every company reconnect Google. Since 11 Nov 2026 the Sources pages no
longer show the survey picker, so new surveys cannot be chosen in the app; the backend is
unchanged.

**Code:** `app/sources/google_forms.py`, `app/insights/sentiment.py` (flag `GOOGLE_FORMS_ENABLED`)

## 24. Answer fact-checker

**Status: Built, switched off : lettucedetect and jev**

A second model checks each claim in an answer against the passages it came from. It is off
because the hosting it needs now requires a paid plan.

**Code:** `app/rag/audit.py`, `deploy/lettucedetect-space/`

## 25. Agent mode (writes into tools)

**Status: Built, on `main`; off until a company's admin turns it on**

Agent mode drafts work in connected tools: a Linear or GitHub issue, a Notion page, a Slack post,
a Google Doc or Sheet. Nothing runs until the person approves a card showing exactly what will be
sent; they can edit the words, ask for changes, or cancel.

- **Through MCP only.** Every write goes through an MCP server an admin added. Linear, Notion,
  GitHub, Slack and Google come as presets; Slack and Google run on Handbook's own MCP servers
  over those tools' regular APIs. An admin can add any other MCP server by its address.
- **Each tool starts off.** The admin sets each one to off, read (used while drafting) or write
  (needs a card). A space's owner can turn tools off for that space.
- **Own accounts.** Each person signs in to each tool with their own account, which must be the
  company's. Never a shared or admin key.
- **Private stays private.** Private content is never sent somewhere public; a wider or unknown
  audience needs a tick on the card.
- **From Slack.** A message that asks for an action, or starts with `agent`, drafts a card: in a
  DM as the person, in a channel from that channel only, shown only to the asker.
- **Recorded.** Every proposal and outcome is kept. Admins see the switches, never what people
  drafted or sent.

**Limitation:** works best with strong models; smaller models have not been tuned for it.

**Code:** `app/actions/`, `app/mcphost/`, `app/api/actions.py`, `app/api/slack_act.py`

## 26. Each company's own Google and Slack app

**Status: Built, on `main`**

An admin sets up the company's own Google app and Slack app under **Sources**, step by step, with
every value to paste shown there. Sync, sign-in and Agent mode then use it. Hosted Handbook
requires the company's own app; a self-hosted install can keep using the app in its `.env`.
Saving an app asks for that tool's connections to be reconnected, because a token belongs to the
app that issued it.

**Code:** `app/auth/org_apps.py`, `app/api/connector_apps.py`, `frontend/components/OwnAppSetup.tsx`

## 27. Usage limits

**Status: Built, on `main`**

- **Requests.** Each person and each company has a question rate; sign-in emails have their own.
- **Answers at once.** At most 20 answers are written at once across the servers, and 2 per
  person; the rest wait for a place, then are told Handbook is busy.
- **AI limits, two layers.** Tokens a minute for each answering model, for the person and for the
  company, input and output counted apart; and dollars a day for the person and the company,
  whichever model answers. Defaults: $0.30 a person and $3 a company a day.

Limits are counted in Redis when it is set up, so every server shares them. Redis holds rate
limits only: there is no answer cache, and every question is answered fresh.

**Code:** `app/security/rate_limit.py`, `app/security/ai_spend.py`, `app/ratelimit/`

## 28. Personal identifiers masked

**Status: Built, on `main`**

Before any text reaches an outside AI service (the models, remote search, the safety checks),
Aadhaar, Virtual ID, PAN, card, mobile, IFSC and UPI values are replaced with placeholders, and put
back only in what the person sees. If the check fails, nothing is sent.

**Limitation:** bank account numbers, order ids, names and emails are not masked.

**Code:** `app/privacy/`, `app/llm/masking.py`

## 29. Answers worked out from sheet rows

**Status: row lookup on; worked figures built, switched off**

A question that types a value from a stored table ("who closed L-1076?") reads that row
directly. With `DOCTABLES_ANSWERS_ENABLED` on, a question asking for a figure over a sheet (a
total, a share, the highest, named people compared) is worked out exactly: the AI fills in a
form, code checks every name and value against the real table, and SQL computes the answer. A
name no table holds is said, never answered as zero.

**Code:** `app/doctables/store.py`, `app/insights/table_answers.py`

## 30. Self-hosted install and Updates

**Status: Released (0.2.0 to 0.4.1)**

A company can run Handbook on its own server from versioned Docker images: the installer creates
`.env`, the team fills it in, and the second run starts Handbook and creates the company and its
first admin. Admin › Updates shows the version running and offers a newer release (one click, or
`./upgrade.sh <version>`); admins also get an email about each release. Cloud has no Updates page:
it is kept current for its companies.

**Code:** `deploy/selfhost/`, `deploy/updater/`, `app/updates/`, `app/api/updates.py`

## 31. Release notes and the new-version pill

**Status: Built, on `main`**

Each release has notes in `release-notes/<version>.md`. A tab left open from an older release
shows a "new version is ready" pill with Refresh (and What's new, for admins). Admins also see a
short notice beside the chat modes when a new version is out.

**Code:** `release-notes/`, `frontend/components/NewVersionPill.tsx`, `frontend/components/ReleaseChip.tsx`

---

## Blockers at a glance

- **Notion per-page access:** Notion's API has no sharing information.
- **Google Groups:** waiting on a Google Workspace admin connection.
- **Google employee directory:** we can't see which employees belong to a group email. Reading
group membership needs Google's Admin SDK APIs, which only a Workspace admin can authorize.
- **Linear private teams:** waiting on a Business-plan workspace to test.
- **GitHub per-person access:** each member must link their GitHub account.
- **Answer fact-checker:** waiting on a paid host.

## Testing

- **Automated tests.** About 2,700 test functions in 218 modules cover the product, run on every change.
- **Staging (1 Oct).** Tested by hand: Slack private-channel access, Linear public-team access,  
chart access, email change, and instant updates from all four tools.
- **Not yet tried live:** Google Groups, Google Forms, Linear private teams, each company's own
Google and Slack app end to end.

## What's next

1. Add new connectors (Confluence and Jira first).
2. Tune Agent mode for smaller models.

