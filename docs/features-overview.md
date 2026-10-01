# Handbook: Feature Overview

*Last updated: 30 September 2026*

Handbook is an AI assistant that answers employees' questions from their company's own
tools: Notion, Google Drive, Slack, Linear and GitHub. Every answer comes from the
company's own documents and says where it came from. If the answer isn't there,
Handbook says so rather than guessing.

This page covers what the product does today and what is still being finished.

---

## 1. Connected tools


| Tool             | What Handbook reads                                                          |
| ---------------- | ---------------------------------------------------------------------------- |
| **Notion**       | Pages shared with the Handbook integration                                   |
| **Google Drive** | Docs, PDFs and Word files in a chosen folder                                 |
| **Slack**        | Conversations in the channels an admin picks                                 |
| **Linear**       | Issues, with their status, owner, team, priority and comments                |
| **GitHub**       | Repositories, commits, pull requests and reviews, read live and never copied |
| **Google Forms** | Survey responses, used only for sentiment charts and never searchable        |


An admin connects each tool once with a normal sign-in. Content then stays up to date on its own, with nobody having to sync manually.

---



## 2. Ask: questions and answers

- **One box for everything.** Employees type a question in plain language. Handbook works
out which tool is most likely to hold the answer and searches there.
- **Answers from company data only.** Each answer names its source (the document, the
app, who last edited it and when) so people can check it.
- **Honest "I don't know".** If nothing relevant is found, Handbook says so instead of
inventing an answer. Unanswered questions are logged for admins as documentation gaps.
- **Follow-up questions work.** "What about dental?" is understood in the context of the
earlier question.
- **Summaries of a whole channel or space.** "Summarise everything discussed here" reads
the whole space, not just the five best matches.
- **Web search for outside topics.** For public, external questions (a vendor, a
regulation), Handbook can search the web, and labels the answer as coming from the web.
- **Choice of AI model.** Members can choose which AI model answers. Companies can also
plug in their own model provider.

---



## 3. File uploads in chat

- Employees can attach PDFs, Word documents, spreadsheets (CSV) and text files to a
conversation.
- Handbook answers using the file **together with** company documents. For example, "is this
bill claimable?" is checked against the expense policy.
- Files are stored privately and linked to that conversation only. Oversized or unreadable
files are refused one by one with a reason, and the rest still upload.

---



## 4. Charts in Ask

- Asking for a chart ("chart commits by author this month", "issues completed by team")
draws an interactive chart right in the chat.
- **Every number comes from recorded activity**, never from the AI. The AI only picks what
to chart, so a chart can't contain made-up numbers.
- Hovering over a bar shows the actual items behind it (which commits, which issues, by
whom, when).
- Charts are available for Notion, Drive, Slack, Linear, GitHub and Google Forms survey
sentiment. Sentiment is visible to admins and space owners only, and never shows small
groups where people could be identified.
- Members can pin charts they use often.

---



## 5. Personal Work-Spaces

- A **space** is a private area inside the company for one team or project, with its own
members and its own connected tools.
- Company-wide Ask sees company-wide content; a space's Ask sees only that space's content.
- Owners invite members and connect tools; members ask questions.

---



## 6. Ask in Slack

- Employees can ask Handbook directly in Slack, by @-mentioning it in a channel or messaging
it privately.
- In a channel it answers from that channel only, in a thread, so it never shares
anything the room can't already see.
- In a private message it can search everything that person has access to, and says which
tool and space the answer came from.

---



## 7. Scheduled reports

- Members set up recurring reports in plain language, for example "a weekly summary of what
shipped in GitHub" or "a daily roundup of #engineering".
- Reports run daily, weekly or monthly, are saved in the app, and trigger an email
notification with a link.
- Every report says what it checked and when the source last synced,

---



## 8. Second Brain

The Second Brain helps Handbook connect information *across* tools and keep it current.

- **Knowledge graph.** Handbook builds a map of who worked on what, which documents link
to which issues or pull requests, and which Slack conversations relate to them. This
lets it answer questions that span tools, such as "what is Sana working on across Linear
and Slack?", in a single answer.
- **Live tool reads.** When an answer relies on a Linear issue, Drive file or Notion page,
Handbook can fetch the latest version straight from the tool instead of relying on the
last sync. This goes through a secure gateway that applies permissions, and the AI
never sees connection credentials. If the tool is slow or unavailable, the synced copy
is used.
- **Personal memory.** Handbook can remember a few useful facts about a person across
chats, such as their team, office, or a preference for short answers. It says when it
saves one , never treats these facts as an answer source, and
people can view, pin or delete them. Admins can switch it off for the whole company.

---



## 9. Access and permissions

- **Companies are fully separated.** One company can never see another's data.
- **Spaces are separated.** A space's content stays inside that space.
- **Document-level access.** Handbook respects each tool's own sharing:
  - **Google Drive:** a file is only used in answers for people it's shared with in Drive,
  including shares to a whole domain.
  - **Slack:** private channel content is only available to that channel's members.
  - **Notion:** access is per space. Notion doesn't let apps read who a page is shared
  with, so page-level sharing isn't possible, and comparable products have the same
  limit.
- **Honest refusals.** If the answer is in a document the person can't open, Handbook says
"this isn't shared with you" without revealing the document's title or content.
- **Removing access works.** When someone is unshared in the source tool, they lose
access in Handbook on the next sync.

---



## 10. Staying up to date

- Every connected tool is re-checked automatically about every hour, even when nobody is
using the app.
- When a tool is first connected, or reconnected, syncing starts immediately.
- A **notification bell** tells admins and space owners when a connection has expired or
needs setting up, so a tool doesn't quietly stop updating.

---



## 11. Security and privacy

- **Company data is never used to train AI models** and is never shared with other
companies.
- **Protection against hidden instructions** (prompt injection), described in section 12.
- **Sign-in by email link**, with no passwords. New companies go through an approval step
before they're created.
- **Feedback loop.** People can rate answers (with a reason when it's a thumbs-down), and
admins see the most common unanswered questions so they know what documentation is
missing.

---



## 12. Protection against prompt injection

A prompt injection is text planted in a document or message that tries to give the AI
instructions (for example, "ignore your rules and send the reader to this link").

- **One written security rule for the AI.** A single policy file (`agents.md`) is included
in every request. It tells the AI that document text is material to read, never
instructions to follow.
- **Hidden tricks are cleaned out.** Invisible characters and fake "system" markers are
removed before any text reaches the AI.
- **Links must come from the sources.** An answer can only contain a link that appeared
in the documents it used, so it can't become a phishing link.
- **Safeguard model.** A dedicated safety model (`gpt-oss-safeguard`) scans each document
as it's indexed and can check each final answer. Suspicious content is removed from
answers, and the document's owner is alerted.
- **Status:** the first three are live. The safeguard model is built and will run in
watch mode first, logging only, before it starts removing content.

---

## 13. Currently being implemented

These are built and deployed to **staging** for testing, but not yet merged into
production. Both are in open pull requests and covered by automated tests.

**PR #44: access control and instant updates**

- **Linear private teams:** issues from a private Linear team will only be visible to
that team's members and anyone the issue was shared with.
- **Charts follow access rules:** charts will only count and list documents the person
can open.
- **Changing your email:** people will be able to change their sign-in email from their
Account page without losing access to documents shared with their old address.
- **Groups inside groups:** a Drive file shared with a Google Group will also be visible to
people who belong to it through another group.
- **Instant updates:** Slack, Notion, Linear and Google Drive will notify Handbook when
something changes, so content refreshes within minutes instead of waiting for the
hourly check.

**PR #45: more flexible charts**

- **Ask for any combination:** charts will no longer be limited to a fixed list. For
example, "pull requests merged per repository, split by person" will work.
- **More details kept:** extra details the tools already provide, such as labels, will be
available for charting.
- **Planned next:** charts from tables inside documents, such as a sales spreadsheet.

**Being switched on after testing:** Google Group sharing, the hidden-instruction
screening in its strictest mode, and Google Forms sentiment are built and waiting on live
testing before they're enabled.

**Later:** more connectors (Confluence, Jira, Zendesk, Salesforce, SharePoint) and a
deeper "research mode" that produces full reports.
