# Handbook: Current Features and Status

*As of 1 October 2026 · code at* `main` *(*`526a899`*)*

Handbook is an AI assistant that answers employees' questions from their company's own tools:
Notion, Google Drive, Slack, Linear and GitHub. Every answer names its source. When the answer
isn't in the company's documents, Handbook says so instead of guessing. People only see what each
tool already lets them see.

## Summary


| #   | Feature                                           | Status                                                                                                                    | Main code                                                  |
| --- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| 1   | Ask: grounded answers                             | Live                                                                                                                      | `app/rag/`, `app/api/chat.py`                              |
| 2   | Smart routing between tools                       | Live                                                                                                                      | `app/agent/routing.py`                                     |
| 3   | Connectors (Notion, Drive, Slack, Linear, GitHub) | Live                                                                                                                      | `app/sources/`, `app/githublive/`                          |
| 4   | Personal Spaces                                   | Live                                                                                                                      | `app/workspaces/`                                          |
| 5   | Access control per document                       | Live (Drive, Slack); Live on staging (Linear public teams, charts); Built, not tested live (GitHub, Linear private teams) | `app/security/visibility.py`, `app/githublive/access.py`   |
| 6   | Second Brain: knowledge graph                     | Live                                                                                                                      | `app/graph/`                                               |
| 7   | Second Brain: live tool reads                     | Live                                                                                                                      | `app/livetools/`                                           |
| 8   | Second Brain: personal memory                     | Live                                                                                                                      | `app/memory/personal.py`                                   |
| 9   | Charts in Ask                                     | Live                                                                                                                      | `app/insights/`, `frontend/components/Chart.tsx`           |
| 10  | Open-ended charts                                 | In progress (PR #45)                                                                                                      | branch `feat/open-ended-charts`                            |
| 11  | Ask in Slack                                      | Live                                                                                                                      | `app/api/slack_events.py`                                  |
| 12  | Scheduled reports                                 | Live                                                                                                                      | `app/schedulers/`                                          |
| 13  | File uploads                                      | Live                                                                                                                      | `app/attachments/`                                         |
| 14  | Model choice and bring-your-own model             | Live                                                                                                                      | `app/llm/`                                                 |
| 15  | Feedback and documentation gaps                   | Live                                                                                                                      | `app/feedback/`                                            |
| 16  | Needs-attention bell                              | Live                                                                                                                      | `app/api/notifications.py`                                 |
| 17  | Automatic sync (hourly)                           | Live                                                                                                                      | `app/jobs/autosync.py`                                     |
| 18  | Instant updates (webhooks)                        | Live                                                                                                                      | `app/api/webhooks.py`, `app/sources/drive_watch.py`        |
| 19  | Prompt-injection protection                       | Live                                                                                                                      | `app/security/`, `app/guard/`                              |
| 20  | Sign-in, email delivery and email change          | Live                                                                                                                      | `app/auth/`                                                |
| 21  | Chat history                                      | Live                                                                                                                      | `frontend/components/RailChats.tsx`                        |
| 22  | Google Groups access                              | Built, switched off                                                                                                       | `app/sources/google_groups.py`                             |
| 23  | Google Forms sentiment                            | Built, not tested live                                                                                                    | `app/sources/google_forms.py`, `app/insights/sentiment.py` |
| 24  | Answer fact-checker                               | Built, switched off                                                                                                       | `app/rag/audit.py`                                         |


---

## 1. Ask: grounded answers

**Status: Live**

One question box covers every connected tool. Handbook finds the most relevant passages using
meaning-based and keyword search together, re-ranks the best 30, and answers only from those
passages. Each answer shows the document, the app, who last edited it and when.

Two safety layers prevent invented answers:

1. If nothing is close enough, Handbook refuses without calling the AI.
2. If passages are found but don't actually answer the question, the prompt makes the AI refuse.

Ask also handles:

- **Follow-ups.** "What about part-timers?" is rewritten into a full question first.
- **Summaries.** "Summarise everything discussed here" reads the whole channel or space.
- **Web search.** One web search is allowed for outside topics, labelled as web. Only words the
user typed are sent.
- **Access refusals.** If the document exists but isn't shared with the asker, the answer says so
and names the tool, never the document.

**Limitations:**

- Very broad questions asked company-wide use the top passages, not every document.
- Summaries are capped at 120 passages or 60,000 characters, oldest dropped first.

**Code:** `app/rag/pipeline.py`, `app/rag/retrieval.py`, `app/rag/access_notice.py`, `app/api/chat.py`

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
- **Google Drive.** Reads Docs, PDFs and Word files in one chosen folder. Google Sheets are not
indexed.
- **Slack.** Reads threads in channels an admin picks. Messages under 15 characters are skipped.
- **Linear.** Reads issues with status, assignee, team, priority, labels and comments.
- **GitHub.** Nothing is stored. Commits, pull requests, reviews, branches and READMEs are read
live through six tools.

Disconnecting a tool deletes everything indexed from it.

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
- **GitHub: built, tested.** A private repository answers only people whose linked GitHub account can open it. Unlinked members see public repositories only.
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

## 9. Charts in Ask

**Status: Live**

Ask for a chart in the same box, for example "chart commits by author" or "pie of Linear issues by
state". Every number comes from counted data in the database. The AI never produces a number.
Hovering a bar shows the items behind it, and charts can be pinned. Charts follow access rules.
Survey sentiment is visible to owners only.

**Limitation:** only a fixed list of metrics can be charted until PR #45 is merged.

**Code:** `app/insights/` (registry, store, resolve, facts), `frontend/components/Chart.tsx`

## 10. Open-ended charts

**Status: In progress (PR #45, under review)**

- **Any combination of splits.** Ask for things like "pull requests merged per repository, split
by person".
- **More fields to chart.** Chart extra fields the tools already return, such as labels.
- **Tables inside documents.** Chart a table inside a document, such as a sales table in a Doc.

The rule stays the same: the AI never produces a number.

**Code:** branch `feat/open-ended-charts`

## 11. Ask in Slack

**Status: Live**

@mention the bot in a channel or send it a direct message, and you get the same answers as in the
app.

- **Channel replies.** Answers go in a thread and use only that channel's content, so private
documents never reach a shared room.
- **Direct messages.** Answers use everything the person can see and name the source and space.
- **While searching.** The bot shows "Searching…" and edits it into the answer.

Unanswered questions are logged here too, but Slack has no thumbs.

**Code:** `app/api/slack_events.py`

## 12. Scheduled reports

**Status: Live (email delivery verified 30 Sep)**

A member describes what they want in plain words and picks a tool, a space and a schedule: daily,
weekly or monthly. Each report is saved in the app and announced by email with a link. The first
report covers the last 90 days. Reports say what was checked, and they describe current content
rather than changes.

**Code:** `app/schedulers/`

## 13. File uploads

**Status: Live**

Attach PDF, Word, CSV, TSV, text, Markdown or JSON files to a chat. Files are stored privately in
Cloudinary and answered together with company documents, so "is this bill claimable?" reads the
bill and the expense policy. Each file gets its own result: one bad file doesn't block the
others.

**Limitations:** no images, and no reusable file library.

**Code:** `app/attachments/`, `app/api/attachments.py`

## 14. Model choice and bring-your-own model

**Status: Live**

Members can pick which AI model answers, from a short vetted list served through OpenRouter and
Groq. Admins can connect their own provider from 14 presets. Model calls through OpenRouter only
go to providers that don't collect data.

**Limitation:** free-tier quotas of hosted models apply.

**Code:** `app/llm/catalog.py`, `app/llm/routed.py`, `app/llm/org_model.py`

## 15. Feedback and documentation gaps

**Status: Live**

Every web answer has thumbs up or down. A thumbs-down asks for a reason: incorrect, out of date,
or unhelpful. Every unanswered question is recorded automatically, from both web and Slack. Admins
see the most-asked missing topics and how many different people asked, on `/admin/feedback`.

**Limitation:** questions are grouped by exact wording, not by meaning.

**Code:** `app/feedback/`

## 16. Needs-attention bell

**Status: Live**

The bell shows tools that stopped syncing or still need setting up. Only the person who can fix
the problem sees it: admins for company-wide tools, and owners for their own space.

**Code:** `app/api/notifications.py`, `frontend/components/NotificationBell.tsx`

## 17. Automatic sync (hourly)

**Status: Live**

Every tool is re-checked every hour. An external timer runs every 10 minutes and keeps the free server awake to avoid render 15 minute stale shutdwn. Only changed documents are re-processed. A new connection syncs right away.

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
to their owner.

**Limitation:** files over about 15,600 characters are not screened by the safety model.

**Code:** `app/security/untrusted.py`, `app/security/links.py`, `app/security/agents.md`, `app/guard/`

## 20. Sign-in, email delivery and email change

**Status: Live (email change verified on production)**

Sign-in uses single-use email links, with no passwords. New companies go through an approval
queue. Emails are sent through SendGrid and verified with real mailboxes. People can change their
sign-in email after confirming the new address. The old address is kept, so files shared to it
still match.

**Code:** `app/auth/magic_link.py`, `app/auth/email_change.py`, `app/auth/email.py`

## 21. Chat history

**Status: Live**

Recent chats appear in the left rail. Each chat has its own link, can be deleted, and is private to its owner.

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
and switching it on makes every company reconnect Google.

**Code:** `app/sources/google_forms.py`, `app/insights/sentiment.py` (flag `GOOGLE_FORMS_ENABLED`)

## 24. Answer fact-checker

**Status: Built, switched off : lettucedetect and jev**

A second model checks each claim in an answer against the passages it came from. It is off
because the hosting it needs now requires a paid plan.

**Code:** `app/rag/audit.py`, `deploy/lettucedetect-space/`

---

## Blockers at a glance

- **Notion per-page access:** Notion's API has no sharing information.
- **Google Groups:** waiting on a Google Workspace admin connection.
- **Google employee directory:** we can't see which employees belong to a group email. Reading
group membership needs Google's Admin SDK APIs, which only a Workspace admin can authorize.
- **Linear private teams:** waiting on a Business-plan workspace to test.
- **GitHub per-person access:** waiting on the production test and members linking GitHub.
- **Answer fact-checker:** waiting on a paid host.

## Testing

- **Automated tests.** 2,058 automated tests cover the product, run on every change.
- **Staging (1 Oct).** Tested by hand: Slack private-channel access, Linear public-team access,  
chart access, email change, and instant updates from all four tools.
- Test GitHub per-person access in production.

## What's next

1. Merge open-ended charts (PR #45).
2. Add new connectors (Confluence and Jira first), a deep research mode, and action tools that
  create issues or post updates after confirmation.

