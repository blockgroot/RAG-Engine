# Product Status — November 13, 2026

> **The canonical source of truth for what Handbook does today.** Product behavior verified
> against `5901c13` (PR #44, 1 Oct 2026). Charts (§4, §5, §12) updated against `783da74`
> (13 Oct 2026), after open-ended charts (PR #45) and the chart fixes (PR #50–#52) merged.
> Brought up to date with `main` at `b4e129c` (13 Nov 2026): everything that was on a branch in
> §12 has merged; those features are now in §2–§9, labelled **Merged** where no live walkthrough
> is recorded.
> Design reasoning lives in `CLAUDE.md`; decision history in git.
>
> **Keep it fresh:** whoever ships, verifies or blocks a feature updates this file in the same
> pull request. Every status carries a date and how we know.

**Status labels used below**

| Label | Meaning |
|---|---|
| **Live** | Enabled in production and tested by the team. |
| **Live (staging-verified)** | Merged to `main`; walked through live on staging. Production may still need configuration (§10). |
| **Built, not verified** | Merged, covered by automated tests, never run against the real service. |
| **Built, off** | Merged, off by default, needs an external prerequisite before it can be switched on. |
| **Blocked** | The provider or plan does not allow it. Work on our side cannot change that. |
| **Merged** | On `main` and covered by automated tests; no live walkthrough recorded here yet. |
| **In progress** | On an open pull request, not merged. |

**Code defaults vs production.** Several features are off by default in the code (`app/config/settings.py`)
and switched on by environment settings in production: the knowledge graph (`GRAPH_RETRIEVAL_ENABLED`),
live tool reads (`LIVE_TOOLS_ENABLED`), personal memory (`PERSONAL_MEMORY_ENABLED`), the
prompt-injection guard (`GUARD_MODE`) and real email (`EMAIL_SENDER=sendgrid`, or `smtp` or
`resend`; the code default `console` is for local development only). Worked figures over sheet
rows (`DOCTABLES_ANSWERS_ENABLED`) are off by default. Their production status below is the team's verification
as of 1 Oct 2026.

---

## 1. Product Overview

Handbook is a multi-tenant AI assistant that answers employees' questions from their company's
own tools: **Notion, Google Drive, Slack, Linear and GitHub**. Answers come only from the
company's own documents and name their source; when the answer is not there, Handbook says so
instead of guessing. Around that core it draws charts, sends scheduled reports, answers inside
Slack, remembers a little about each person, connects information across tools, respects each
tool's own sharing rules and stays current by itself. In Agent mode it drafts writes into the
connected tools and runs each only after the person approves it. It runs as Handbook Cloud or
self-hosted on a company's own server.

**Problem it solves:** company knowledge is scattered across tools, and search returns lists of
links. Handbook returns one checked answer, with its source, limited to what the asker may see.

**Maturity:** in production with real tenants. The core product, Second Brain, security layers
and access control are live; the remaining gaps are external (provider APIs, plans, admin
rights, §11) or on an open pull request (§12).

## 2. Current Product Capabilities

| Area | Capability | Status |
|---|---|---|
| Ask | Grounded answers with sources, honest refusals, follow-ups, whole-space summaries, web search for outside topics, choice of AI model | Live |
| Chat | Private chat history in the left rail, linkable chats, rename, pin, delete | Live (rename and pin: Merged 12 Nov) |
| Files | Upload PDF, Word, Excel, CSV, TSV, text, Markdown, JSON files into a chat; stored in Cloudinary, or in Postgres without it; their tables and figures can be charted | Live |
| Charts | Chart mode: charts from tool activity, from tables and paragraph figures inside documents, and from uploaded files; ECharts on the web, PNG in Slack | Live |
| Second Brain | Knowledge graph across tools, live re-reads of current state, personal memory | Live |
| Spaces | Private areas with their own members and tools | Live |
| Slack | Ask the bot in channels and DMs; charts as images with a link to the interactive chart | Live |
| Reports | Scheduled reports by email | Live |
| Feedback | Thumbs with reasons; automatic documentation-gap list for admins | Live |
| Access | Company and space isolation; per-document access for Drive, Slack, Linear; per-repo for GitHub; charts follow access | Live / staging-verified (§6) |
| Freshness | Instant updates from Slack, Linear, Notion and Drive, plus an hourly re-check | Live |
| Security | Prompt-injection defense, link provenance, safety model, encrypted tokens, magic-link sign-in | Live |
| Privacy | Personal identifiers masked before text reaches an outside AI service | Merged |
| Agent mode | Writes into connected tools through MCP servers, each approved on a card | Merged; off until a company's admin turns it on |
| Limits | Request rate limits, answers at once, AI limits in tokens a minute per model and dollars a day | Merged |
| Install | Handbook Cloud, or self-hosted from versioned images with Admin › Updates | Released 0.2.0–0.4.1 (a test install of 0.2.1 was walked through) |

## 3. Feature-by-Feature Status

| Feature | What it does | Status | Verification | Limitations |
|---|---|---|---|---|
| Grounded Q&A | Retrieves the most relevant passages (vector + keyword search, re-ranking a pool of 40) and answers only from them | Live | Golden-set evaluation in CI; unit suite | Very broad questions in company-wide Ask use ranked passages, not every document |
| Relevance gate + strict prompt | Refuses before calling the AI when nothing is close enough; the prompt refuses when passages don't answer | Live | `tests/test_grounding.py`, golden set | Threshold 0.35, calibrated on a small sample |
| Routing | Picks which tool answers by comparing the question to each tool's content; a named tool or repo wins | Live | `tests/test_agent_routing.py` | A misroute costs a refusal, never a wrong answer |
| Follow-ups and memory of the conversation | Rewrites "what about dental?" into a full question; summarises long chats | Live | `tests/test_conversation*.py` | — |
| Whole-space summaries | "Summarise everything discussed here" reads the whole channel or space | Live | `tests/test_whole_scope_read.py` | Capped at 120 passages / 60k characters; oldest dropped first |
| Web search | For external topics only, clearly labelled; sends only words the user typed | Live | `tests/test_websearch.py`, `tests/test_outbound_query.py` | DuckDuckGo; one search per question |
| Model choice / company's own model | Members pick Claude Sonnet 5.5 or Claude Haiku 5.5 (11 Nov); admins can add the company's own model from one of 14 provider presets; background work runs on the aux model | Live | `tests/test_model_selection.py`, `tests/test_llm_adapters.py` | — |
| Chat history | Recent chats in the rail, `?c=` links, rename, pin, delete; each chat private to its owner | Live (rename and pin: Merged 12 Nov) | `tests/test_conversation_history.py`; UI checked by hand (30 Sep) | No automated browser test |
| File uploads | Files join retrieval for the chat they're in | Live | `tests/test_attachment*.py` | No images; no reusable file library |
| Charts | The AI fills in a request; code checks it and SQL adds up stored rows. The AI never produces a number | Live | `tests/test_insights_*.py`, `tests/test_doctables*.py`, `npm run check:charts` | GitHub charts are space-level; figures are charted only when a sentence states them (§4) |
| Spaces | Private sub-workspaces; members see only their space's content | Live | `tests/test_workspaces.py`, `tests/test_isolation.py` | — |
| Ask in Slack | Channel answers in a thread from that channel only; DMs answer as the person; charts as PNG images | Live | `tests/test_slack_bot.py`, `tests/test_slack_chart_upload.py` | No thumbs in Slack (gaps are still logged); Slack charts are static images |
| Scheduled reports | Daily/weekly/monthly, saved in-app, email notification | Live | `tests/test_scheduler_*.py`; emails verified live (30 Sep) | Reports describe current content, not diffs |
| Feedback and gaps | Thumbs with reasons; refusals logged automatically; grouped by meaning into topics; company topics open from a bell alert (`/admin/feedback`, not in the sidebar), a space's on its page for its owner | Live 15 Oct | `tests/test_feedback.py`, `tests/test_gap_topics.py` | Grouping: embeddings pick candidates (similarity 0.40+), the background model decides; checked on a made-up set, not yet on real questions |
| Notification bell | Expired or unscoped connections, topics 3+ people couldn't get answered (or 2+ marked wrong) in 14 days, documents the guard leaves out (opening the page itself), and self-hosted releases, shown to whoever can fix them; dismiss one or clear all (12 Nov) | Live (gap alerts: 15 Oct) | `tests/test_notifications.py`, `tests/test_gap_topics.py` | A dismissal is kept per browser |
| Knowledge graph | Who worked on what across tools; one answer can span tools | Live | `tests/test_graph_*.py` | — |
| Live tool reads | Re-reads a Linear issue, Drive file or Notion page when the question is about current state | Live | `tests/test_live_tools.py`; Notion and Drive verified on staging | Slack reader kept off (Slack's rate tier) |
| Personal memory | Remembers a few facts a person states; Undo; manage on `/account` | Live | `tests/test_personal_memory.py` | Web chat only; no memory-free private chat mode |
| Document-level access | Each tool's sharing rules applied before ranking | Live / staging-verified | `tests/test_doc_access.py`, `tests/test_visibility.py` | See §6 per tool |
| Email change | Change sign-in email; old address kept so existing shares still match | Live (staging-verified 1 Oct) | `tests/test_email_change.py` | — |
| Instant updates | Webhooks from Slack, Linear, Notion; Drive push channels | Live (configured in production 1 Oct) | `tests/test_webhook_sync.py`; staging timings §7 | Each tenant reconnects Linear once (§10) |
| Prompt-injection defense | Policy file, scrubbing, link provenance, canary, safety model | Live | `tests/test_untrusted_*.py`, `test_link_provenance.py`, `test_injection_*.py`, `test_exfil_channels.py`, `test_red_team_corpus.py` | §8 |
| Google Groups | Files shared with a Google Group (incl. nested) open to its members | Built, off | `tests/test_nested_groups.py` | Needs a Workspace-admin connection (§11) |
| Google Forms sentiment | Survey sentiment charts, never searchable | Built, not verified | `tests/test_insights_sentiment.py` | Never run on a real form; needs every tenant to reconnect Google |
| Answer fact-checker | Second model checks each claim against the passages | Built, off | `tests/test_audit_lettuce.py` | Hosting needs a paid plan (§11) |
| Deep analysis and Extra effort | Deep reads the top documents in full; Extra effort also reads wide for list and company-wide questions (§12) | Merged | `tests/test_answer_depth.py` | Extra effort costs more tokens per question |
| Sheet rows in Ask | A question that types a cell reads that row; with `DOCTABLES_ANSWERS_ENABLED` a figure over a sheet's rows is worked out by checked SQL | Row lookup: Merged; worked figures: Built, off | `tests/test_table_answers.py`, `tests/test_doctables*.py` | Off by default: one more question check per Ask |
| Agent mode | Drafts writes into Linear, GitHub, Notion, Slack, Google and any MCP server an admin adds; each runs only after approval | Merged; off until a company's admin turns it on | `tests/test_actions_*.py`, `tests/test_mcp_*.py` | Tuned for strong models only |
| Company's own Google and Slack app | Sync, sign-in and Agent mode use the company's own app, set up under Sources | Merged (3 Nov) | `tests/test_own_connector_apps.py` | Not yet walked through with real Google and Slack apps |
| Company email domain | Invites and email changes must match the first admin's domain | Merged (6 Nov) | `tests/test_api_admin.py`, `tests/test_email_change.py` | Exact match; a subdomain is another domain |
| Rate limits | Token buckets per person and per company, in Redis when set; at most 20 answers written at once, 2 per person | Merged (4 Nov) | `tests/test_rate_limit.py`, `tests/test_slack_rate_limit.py` | Without `REDIS_URL` each server counts on its own |
| AI limits | Tokens a minute per answering model (person and company, input and output apart) and dollars a day | Merged (11–13 Nov) | `tests/test_ai_spend.py` | A model not priced is not charged |
| Identifier masking | Aadhaar, Virtual ID, PAN, card, mobile, IFSC and UPI values replaced before text leaves | Merged | `tests/test_privacy_masking.py` | Bank account numbers, order ids, names and emails are not masked |
| Self-hosted install and Updates | Installer, one company per install, Admin › Updates, release emails | Released 0.2.0–0.4.1 | `tests/test_updates.py` | — |

## 4. Knowledge & Intelligence

- **RAG / knowledge retrieval.** Documents are split into passages (256 tokens, 40 overlap)
  with a short context line, embedded, and searched by meaning and by keyword. The two lists
  are fused and re-ranked from a pool of 40 down to the top 5. Each
  passage reaches the AI with its document, app, last editor and date, so "who wrote this?"
  and "when was it updated?" are answerable.
- **Second Brain** is three layers, all live:
  - **Knowledge graph.** Built from what each sync already saw (authors, assignees, links between
    pages, issues and pull requests, private-channel membership). It lets one answer read a
    second tool ("what is Sana working on across Linear and Slack?"). People are linked across
    tools only on proof (matching sign-in email, or a GitHub account they linked), never by name.
    Every step through the graph checks the asker's access.
  - **Live tools.** When the question is about current state ("is SYV-5 still blocked?"), the
    synced copy is refreshed from the tool itself, at most 2 items, within 6 seconds. A deleted or
    unshared item is withheld rather than shown stale. Linear is on by default; Drive and Notion
    readers are built and were verified on staging; Slack's reader is kept off because of Slack's
    rate limits.
  - **Personal memory.** Facts a person states about themselves ("I'm in the Bangalore office",
    "keep it short") are saved with an Undo, used to interpret later questions, and never used as
    evidence. Sensitive topics are never stored. People manage them on `/account`; admins can turn
    memory off for the company.
- **Charts** (Chart mode, web and Slack). The AI fills in a request: what to count, how to
  split it, the period, the shape. Code checks every part against real rows and SQL adds them
  up; a request that can't be checked is refused with the reason. **The AI never produces a
  number.** Three kinds of data:
  - **Tool activity** (`activity_facts`): 12 measures (Notion and Drive changes; GitHub commits,
    pull requests, reviews, lead time; Linear issues, states, cycle time; Slack conversations;
    Forms sentiment) plus any simple field a tool returns, discovered per space. GitHub is read
    fresh when a chart asks for it. GitHub charts are still space-level, not per person.
  - **Tables in documents** (`doc_tables`, `doc_table_rows`): Notion tables and inline
    databases, Docs, Sheets, Excel, CSV and Word tables, saved as rows at sync with no AI. A
    workbook is read tab by tab; tabs with the same header are one table with a `Tab` column.
    Two tables can be compared in one chart ("target vs achieved", `ChartSpec.versus`).
  - **Figures written in paragraphs:** at sync, a page with three or more figures is read by
    the background AI model, the whole page in sections. The AI lists each figure (measure,
    unit, period, label, quote); code builds the tables and keeps a figure only when its quote
    is in the page and the number binds to its label. No word lists, any language. A damaged
    AI reply keeps its complete figures; an unreadable one is retried by up to 3 later syncs and
    shown on the admin Feedback page. Chart mode reads a named or closely matching page live
    when nothing is saved.

  Shapes follow the data (`registry.shapes_for`): bar, horizontal and stacked bar, line, area,
  pie, donut, heatmap. A document table keeps its own row order and uses its first column as
  the axis; percentages are averaged, never summed. Each chart names its measure and unit and
  says what a bar counts. Charts follow the same access rules as answers.
- **Sheet rows in Ask.** A question that types a cell of a stored table ("who closed L-1076?")
  reads that row as a passage, found through the cell index. With `DOCTABLES_ANSWERS_ENABLED`
  (off by default), a question asking for a figure over a sheet (a total, a share, the highest,
  named people compared) is worked out by `insights/table_answers.py`: the model fills a form,
  code checks every name and value against the real table, and SQL computes it. A name no table
  holds is said, never answered as 0; a filter the model read into the question is worked out
  both as the whole and as its part.
- **Knowledge-gap management.** Every unanswered question is recorded automatically (web and
  Slack) and grouped with others that ask the same thing in other words (embeddings pick
  candidates, the background model decides). When 3 different people can't get an answer on
  one topic in 14 days, or 2 mark its answers wrong, the bell tells whoever can fix it: the
  admin for company-wide questions, the space's owner for that space's. The alert opens the
  topic; there is no sidebar page. Counts only, never who asked. A question withheld for
  access reasons is not counted as a gap, and charts Handbook couldn't draw are not shown.
- **Feedback.** Thumbs up/down on every web answer; a thumbs-down asks why: incorrect, out of
  date, or unhelpful.
- **Web and tools.** One web search for external topics when company documents don't answer.
  GitHub is answered through six live tools (README, commit, commits, pull requests, reviews,
  branches) rather than stored copies.

## 5. Files & Documents

- **Upload:** PDF, DOCX, XLSX, CSV, TSV, TXT, Markdown, LOG and JSON, several at once. Each file is
  checked separately; unreadable, empty, oversized, over-length (too many tokens) or over-limit
  files are refused with a reason while the others upload.
- **Processing:** text is extracted once at upload. CSV and TSV skip the length limit because
  narrow questions are answered by paging through rows.
- **Storage:** Cloudinary, as private authenticated assets; with no Cloudinary keys (a
  self-hosted install), the extracted text is kept in Postgres. The original file and its extracted
  text are stored side by side, so a large PDF is not re-read on every question. Download links
  are signed and expire. Deleting a file or chat deletes its assets; a sweep removes expired
  ones.
- **Retrieval:** a file joins whatever the question retrieves from company documents, so "is
  this bill claimable?" is answered against the bill and the expense policy together. If the
  company documents don't match, the answer comes from the file alone.
- **Charts from files:** Excel sheets, CSV, Word tables and PDF tables are saved as rows at
  upload, with no AI, and never fail the upload. Figures in a file's sentences are read once,
  in Chart mode. Only the uploader, in that chat, can chart a file; removing it removes its tables.
- **Indexed documents:** Google Docs, PDF, Word, Google Sheets, Excel and CSV files in the chosen
  Drive folder (a spreadsheet embeds a description of its columns, never its figures); Notion
  pages shared with the integration; Slack threads; Linear issues.

## 6. Connectors & Integrations

| Connector | What is supported | Authentication | Access control | Limitations | Testing |
|---|---|---|---|---|---|
| **Notion** | Pages shared with the integration, including nested blocks | OAuth (public integration) | **Per space only.** Everyone in the space can get answers from every shared page | Notion's API exposes no page sharing (§11) | Live; push verified on staging 1 Oct |
| **Google Drive** | Docs, PDFs, Word files, Google Sheets, Excel and CSV in one chosen folder | Google OAuth (`drive.readonly`, `documents.readonly`) through the company's own Google app | **Per file**, from Drive's own sharing: people, whole domain, anyone-with-link. Unreadable sharing falls back to the connected account only. Removals apply on the next sync | Groups need an admin (§11) | Per-file sharing verified live 30 Sep; push verified on staging 1 Oct |
| **Slack** | Threads in channels an admin picks; the bot in channels and DMs | Slack OAuth (bot scopes incl. `chat:write`) through the company's own Slack app | **Per channel**: public channels open to the space; private channels only to members. A channel reply uses only that channel's content | Short one-liners under 15 characters are not indexed | Private-channel access and push verified on staging 1 Oct |
| **Linear** | Issues with status, assignee, team, priority, labels, comments | Linear OAuth (`read` scope) | **Per team**: public teams open to the space; private and restricted teams only to members, workspace admins and anyone an issue was shared with | Private teams untestable without the Business plan (§11) | Public teams and push verified on staging 1 Oct |
| **GitHub** | Commits, pull requests, reviews, branches, READMEs, read live; nothing stored | GitHub App installation | **Per repository**: public repos open to the space; private repos only for people whose linked GitHub account can open them | Members must link GitHub; charts built from GitHub activity are still space-level | Verified in production 1 Oct |
| **Google Forms** | Sentiment of chosen surveys, never searchable | Same Google connection, extra scope | Admins and space owners only; groups under 5 responses hidden | Never run on a real form; the survey picker was removed from Sources (11 Nov) | Automated tests only |

**How access is enforced.** Every document stores who may read it, captured from the tool at
sync time. Retrieval adds one condition in the same database query that already restricts to
the company: "public in this space, or shared with this person". It applies before ranking, on
every search path, on charts, starter suggestions, scheduled reports, live reads and the
knowledge graph. People are matched by email (including prior sign-in emails), so a file shared
with someone before they sign up starts working the day they log in. Each Sources card states in
one line who can get answers from that tool.

**Each company's own Google and Slack app** (merged 3 Nov). An admin sets up the company's
Google app (Internal: no test users, no Google review) and Slack app under **Sources**, one step
at a time, with every value to paste shown there; sync, sign-in and Agent mode then use it. Hosted
Handbook leaves the shared app's keys unset, so there the company's own app is required; a
self-hosted install can keep using `.env`. Saving an app flags that tool's connections for
reconnect, because a token belongs to the app that issued it. A company's Slack app calls
`/slack/events/<app_key>` and `/slack/interactivity/<app_key>`.

## 7. Communication & Event Integrations

- **Email (SendGrid) — Live.** Sign-in links, signup approvals, scheduled report notifications
  and email-change confirmations are sent through SendGrid and verified with real mailboxes. SMTP
  and Resend are the other senders (a self-hosted install picks one). Sign-in and invite emails
  open with a button and carry no click tracking (12 Nov). The `console` sender exists only for
  local development.
- **Webhooks — Live (configured in production 1 Oct; timings measured on staging):**

  | Source | How | Measured on staging |
  |---|---|---|
  | Slack | Events API, signed; channel messages trigger a sync, never a reply | sync starts < 1 s, searchable ~20 s |
  | Linear | OAuth-app webhook, signed, timestamp-checked | sync starts 0.7 s |
  | Notion | Integration webhook subscription, signed | searchable ~30 s |
  | Drive | Google push channel per connection, renewed weekly | searchable ~3 min |

  A burst of changes makes at most one sync every 3 minutes. Each receiver is closed until its
  secret is configured.
- **Slack bot** posts "Searching…" and edits it into the answer; link previews are off. A chart
  is uploaded as a PNG (needs `files:write`; without it the numbers are written as text) with an
  **Open interactive chart** link that re-asks the question in the web app under the clicker's
  own access. A DM charts what the person can open, trying their other spaces if needed; a
  channel charts that channel only, and its link opens the channel's space.
- **Hourly re-check** of every tool, driven by an external tick, catches anything a webhook
  missed.

## 8. Security & Safety

**Implemented and live:**
- **Tenant isolation:** every query is pinned to one company, and inside it to one space.
- **Authentication:** passwordless email links (single use, server-side), signed httpOnly session
  cookies, a reviewed signup queue for new companies, rate limits on a trusted client address.
- **Secrets:** connector tokens are encrypted at rest; the webhook secrets are verified with
  signatures; the internal tick needs its own secret.
- **Prompt-injection defense:**
  - One policy file for the AI (`app/security/agents.md`) is placed before and after every block
    of outside text: document text is material, never instructions.
  - Outside text is cleaned first: invisible characters, fake "system" markers and forged
    boundaries are removed.
  - **Link provenance:** an answer may only contain links that appeared in what the AI was shown;
    Slack link previews are off. Every published leak of this kind left through a link.
  - A **hidden canary** catches the AI repeating its instructions.
  - A **safety model** (`gpt-oss-safeguard`) scores documents at indexing time and can check
    answers. Flagged documents are reported to their owner, never to the asker; ordinary
    questions are logged, never refused.
  - The AI's own stored text (context lines, chat summaries) is treated as untrusted too.
- **Privacy:** tenant data is never used for training; model calls through OpenRouter require
  no-data-collection providers.

**Merged, live walkthrough not recorded:**
- **Partitioned `chunks` with row-level security** (30 Oct): one partition per company, made and
  dropped with the company; pooled connections see only the rows of the company they were opened
  for, and none without one (`tests/test_chunk_partitions.py`).
- **Embeddings as `halfvec`** (2 Nov): the index is a third of its old size, and every pooled
  connection sets `hnsw.iterative_scan = strict_order`, so a company's search returns the rows it
  asked for from the shared index (at 1.1M vectors it returned 3.9 of 10 without it). Needs
  pgvector 0.8+ (`tests/test_halfvec.py`).
- **Identifier masking:** Aadhaar, Virtual ID, PAN, card, mobile, IFSC and UPI values are replaced
  with placeholders before any text goes to the AI models, the remote search or the guard, and
  restored in what the person sees. A detector failure blocks the send. `MASK_IDENTIFIERS=false`
  is the kill switch (`tests/test_privacy_masking.py`; git history:
  docs/plans/2026-10-21-masking-personal-identifiers-before-ai.md).
- **Company email domain** (6 Nov): a company's domain is its first admin's
  (`organizations.email_domain`). Inviting someone, or changing a sign-in email, to another
  domain (a personal address, or a subdomain) is refused.
- **Rate limits** (4 Nov): token buckets per person and per company, in Redis (Valkey,
  `volatile-lru`) when `REDIS_URL` is set, else per process. At most 20 answers are written at
  once (2 per person); the rest wait for a place. Redis holds rate limits only: the answer cache
  was removed (6 Nov), so every question is answered fresh. Each refusal is logged with the
  limit's name, never the question.
- **AI limits in two layers** (11–13 Nov): tokens a minute per answering model, input and output
  apart, for the person and the company (`AI_TOKEN_LIMITS`), and dollars a day in one wallet
  whichever model answers (`AI_PERSON_DOLLARS_PER_DAY` 0.30, `AI_COMPANY_DOLLARS_PER_DAY` 3.00,
  priced by `AI_PRICES`). Background work on the aux model is not charged
  (`tests/test_ai_spend.py`).
- **Injection guard on its own account** (12 Nov): `GUARD_API_KEY` and `GUARD_BASE_URL` (both or
  neither) and `GUARD_SAFEGUARD_MODEL`, so the guard never spends the answering models' quota.

**Known limitations:** no detector catches every reworded attack, which is why the protections
assume the model can be fooled and make that harmless; uploads over ~15,600 characters are not
scored by the safety model; the answer fact-checker is off (§11).

## 9. User Experience

- **Ask** is one box with a source pill that names the tool and space each answer came from.
- **Citations:** one source is named once under the answer. Two or more keep a gray superscript
  after the sentence, matched to a Sources list. The link is the address stored at sync. The
  list is stored on the turn, so a reopened chat shows the same sources. Slack strips the numbers.
- **Chat history** in the left rail, with linkable, deletable, private chats. Rename and pin from
  the ⋯ menu; pinned chats stay at the top (12 Nov).
- **Answer progress** (12 Nov): while an answer is written, a timeline shows which tools were
  searched (with their logos) and which documents were found.
- **Uploads** attach to a chat with a per-file result.
- **Charts** (Chart mode beside the message box) are drawn with ECharts: hover shows the rows
  behind a bar; table view, PNG and CSV download, pins. A reopened chat shows the chart as drawn.
  Starter questions come from the space's own metrics and fields.
- **"Remembered · Undo"** under an answer when memory saved a fact.
- **Refusals that help:** "I don't know", "this exists but isn't shared with you", "link your
  GitHub account", or "ask me in a direct message", depending on the reason.
- **Account page:** sign-in email (change with confirmation), linked GitHub account, personal
  memory.
- **Admins:** Sources (connect, choose folders and channels, who-can-see notes), feedback and
  gaps (Feedback in the sidebar: unanswered questions, refused charts, figure reading), the
  needs-attention bell, model settings, people.
- **Public site** (6 Nov 2026, Live once deployed; checked locally in a browser): the home page
  shows the product as it is (mode figures, admin controls, a section on each company's own
  Google and Slack app), and a **Deployment** page (`/deployment`, in the site nav) explains
  Handbook Cloud and Self-Hosted: setup, what runs on the server, the install, and how a new
  version reaches a self-hosted install. Public pages name no competitor, company or AI model.
- **In-app redesign** (6 Nov 2026, Live once deployed; checked locally in a browser): a dark
  green menu that no longer re-animates on each page. Ask has a mode switch above the box,
  starters with each source's logo, Deep starters that ask for whole documents
  (`deep_questions` from `GET /chat/suggestions`), chart starters drawn as their chart (at
  least three whenever the scope can chart anything), and Agent's example requests only for
  tools the scope has. Each space has its own colour from its name (list cards, its page, the
  People drawer); a space's page splits connected sources from ones to add. Reports are set up
  in one composer (where it reads, how often) and listed as a timeline by next run. Admin
  pages (Sources with each company app's setup as steps, Model, People, Actions) and both
  "What we couldn't answer" pages share the same calm layout. Ask's starters sit in an even
  two-column grid. A report's activity list shows who, where and when above one clean line
  (the Slack chunk header and `*` marks stripped), the first six, then "Show all".
- **Sources pages tidied** (11 Nov 2026): connected tools sit in a two-column grid, each card
  in its tool's colour with one access line (lock) and one status line ("Synced 8 Nov · 18
  pages · checked 3 days ago"), buttons at the bottom; tools to add are a row of tiles. The
  Google Forms survey card is gone from the company and space Sources pages.
- **Agent mode, Account and connect pages redesigned** (11 Nov 2026): Admin › Actions is one
  large Agent mode switch with the ask → draft → approve steps, tool rows in their colour with a
  meter of tools on, and Slack as two tiles. Account opens with a profile card (avatar, name,
  email), then tool tiles, Agent sign-ins with logos, and the last 5 actions with "Show all". The
  GitHub account chooser and a new setup guide share one card look. Pressing Connect for a tool
  whose keys are not set now lands on `/connect/setup` (what to create, the callback address to
  copy, the settings to fill; Google and Slack point to the company app in Sources) instead of a
  JSON error. A running sync shows "12 of 40 issues done" on every connector's card (it was hidden
  while the change check ran or had found changes), named per tool: pages, files, conversations,
  issues. The Ask home centres its greeting and starters without cutting off the heading.
- **Updates shows the version** (6 Nov 2026): `HANDBOOK_VERSION`, else the version the release
  build writes into the image (`HANDBOOK_IMAGE_VERSION`), else, for a copy run from the source,
  `git describe` ("0.2.1, 66 changes after it"), which is shown but never checked against the
  release feed. Cloud deployed straight from main has none of these, so it shows "Latest build
  from main" and the deploy's commit (`RENDER_GIT_COMMIT`), also never checked.
  Cloud built from git (a commit known) ignores `HANDBOOK_VERSION`: a value left on the host went
  stale on the next push and made Cloud say "0.3.0 is available" while running it. It never offers
  a release and shows the newest release's notes as what's new (11 Nov 2026).
- **Updates is self-hosted only** (11 Nov 2026): Cloud is kept current for its companies, so
  there it has no Updates link, the page sends you to Ask, the bell has no version items, and the
  new-version pill offers only Refresh. `GET /me` says `selfhosted`. Releases are cut for
  self-hosted copies.
- **Release email to self-hosted admins** (11 Nov 2026): the worker's hourly pass reads the release
  list and emails every admin about the newest release once (`update_notices` holds the versions
  announced, claimed before sending; released again if every send failed), with the notes, a
  security flag in the subject, and a link to Updates. Cloud sends none (`app/updates/announce.py`).
- **Delivered reports, per schedule** (12 Nov 2026): the list holds each schedule's latest
  `reports.PER_SCHEDULE` (12) reports, not 50 in all (five daily schedules pushed a weekly one
  off the page); each row carries `runs_total`, and a card says how many older reports it does
  not list. Cards show four earlier dates, then "+N more".
- **"A new version is ready"** (11 Nov 2026): every build has an id (`NEXT_PUBLIC_BUILD_ID`): the
  RELEASE it carries, never the commit (12 Nov: the image's tag, else the newest
  `release-notes/<version>.md`), so an ordinary Cloud deploy of main shows nothing; `/build-id`
  serves the live one. An open tab from an older release shows a pill with Refresh (and What's new for admins), checked
  every 5 minutes and when the tab comes back into view.

- **Company email domain** (6 Nov 2026): a company's domain is its first admin's
  (`organizations.email_domain`, existing companies filled from their earliest admin). Inviting
  someone, or changing your sign-in email, to another domain (a personal @gmail.com, or a
  subdomain) is refused with "Only @<domain> addresses can join this company."
- **Notifications** (12 Nov 2026): one item can be dismissed, or all cleared; a dismissal is kept
  in that browser until the problem changes. A flagged-document item opens the flagged page
  itself. The panel shows each tool's icon.
- **Model picker** (11–12 Nov 2026): Claude Sonnet 5.5 and Claude Haiku 5.5, plus the company's
  own model when an admin added one. Gemini runs background work and is never offered.
- **Release notice** (13 Nov 2026): admins see a short notice beside the chat modes when a new
  version is out. Chart starters are fetched when the chat opens and kept for the visit, with
  placeholder tiles while they load (12 Nov).

## 10. Production Status

**Enabled and working in production (team-verified, 30 Sep – 1 Oct 2026):** Ask with grounding,
citations, routing and web search; chat history; file uploads to Cloudinary; charts; spaces; Ask in Slack;
scheduled reports with real email; feedback and gap tracking; needs-attention bell; Second
Brain (knowledge graph, live reads, personal memory); prompt-injection defense; Drive per-file
access; automatic hourly sync; instant updates from Slack, Linear, Notion and Drive (configured 1 Oct).

**Remaining per-tenant steps** (production webhooks are configured):

| Step | Who |
|---|---|
| **Every tenant reconnects Linear once** (Linear creates the webhook only on a new authorization; until then the hourly re-check covers Linear) | Each tenant |
| Members link GitHub under Account → Linked accounts, to keep answers about private repos | Each member |

## 11. Known Limitations & Blockers

| Feature | Current Status | Blocker / Limitation | Type | What Is Required |
|---|---|---|---|---|
| Notion per-page access | Blocked | Notion's API has no sharing field on pages and no permissions endpoint; teamspaces are hidden. The Enterprise Admin API lists group members but never which group is on which page | External (provider API) | Notion shipping a page-sharing API. Workaround: one Notion connection per space, sharing only that space's pages |
| Google Groups (incl. nested) | Built, off | Reading memberships needs `admin.directory.group.readonly` from a **Google Workspace admin**; with a non-admin account the Google reconnect fails, Drive included. Cross-domain nesting is never resolved by Google | Administrative | A confirmed admin connection, then `GOOGLE_GROUPS_ENABLED=true` and a Google reconnect |
| Linear private teams | Built, not verified | Private teams are a Linear Business-plan feature; the test workspace is on the free plan | Plan | A Business-plan workspace to walk it through |
| Linear instant updates for private teams | Unknown | Linear's docs do not say whether an app webhook covers private teams | External (undocumented) | Settle on a Business-plan workspace; until then those changes arrive through the hourly re-check |
| Linear instant updates for existing tenants | Needs action | An OAuth-app webhook is created only when a workspace authorizes after it is enabled | External | Each tenant reconnects Linear once |
| GitHub private repos | Live (verified in production 1 Oct) | Each member must link their GitHub account; unlinked members get public repos only | Product rule | Members link GitHub |
| GitHub in charts | Limitation | Charts built from GitHub activity are space-level, not per person | Not built | Filter GitHub facts by the asker's visible repos |
| Interactive charts in Slack | Not possible | Slack messages cannot run scripts, so a chart is a PNG plus a link to the web app | External (Slack platform) | — |
| Figures in paragraphs | Limitation | A figure is charted only when a sentence states it with its label; implied or computed figures are skipped | Decided | — |
| Google Forms sentiment | Built, not verified | Never run on a real form; switching it on makes every tenant reconnect Google | Verification | A live walkthrough before enabling `GOOGLE_FORMS_ENABLED` |
| Answer fact-checker (LettuceDetect) | Built, off | Hugging Face now requires a paid plan for the CPU host | Dependency / cost | A paid CPU host |
| More connectors (Confluence, Jira, Zendesk, Salesforce, SharePoint) | Not on `main` | No provider blocker. A Confluence branch exists and is not the shipping product | — | Review and merge, or build the others |
| Images in chat | Not supported | No vision path | Decided | — |
| Frontend tests | Limitation | No automated browser tests; UI checked by hand | Tooling | A browser test setup |

## 12. Recently Merged and In Progress

Open pull requests (13 Nov): a benchmark of encrypted chunk text with keyword search (PR #63) and
a design note on tenant direct access (PR #62); neither changes the product. The items below were
in progress on 13 Oct and are now on `main`; the shorter entries for each are in §2–§9.

**Each company's own Google and Slack app** (PR #64, 3 Nov 2026). Set up under **Sources**; see
§6. Self-hosted installs keep using `.env`. Covered by `tests/test_own_connector_apps.py`; not yet
walked through with real Google and Slack apps.

**Embeddings stored as `halfvec`** (PR #60, 2 Nov 2026). `chunks.embedding` and
`gap_topics.embedding` moved from `vector(1024)` to `halfvec(1024)`; existing rows are converted in
place by `apply_schema()` (a cast, nothing re-embedded; marked `embeddings_halfvec`) and the HNSW
index is rebuilt. Measured: the index is a third of its old size (2,725 vs 8,380 bytes a vector).
Covered by `tests/test_halfvec.py` (local Postgres 17, pgvector 0.8.6); needs pgvector 0.8+.

**Masking personal identifiers** (PR #57). See §8. Covered by `tests/test_privacy_masking.py`;
not yet walked through on staging.

**Agent mode (writes into tools)** (PR #54, then MCP only; git history:
docs/plans/2026-10-22-mcp-only-actions.md). Users see **Agent mode** (chat chip "Agent", Account →
Agent, Admin › Actions; Slack `agent …`, old `act …` still works); code, routes and tables keep
`act`.

- **Off until allowed.** A company's admin turns Agent mode on (the `act` row in
  `action_policies`; no env switch). Every tool starts off; the admin sets each to off, read (a
  lookup while drafting, no card) or write (a card). "Turn all on" turns on everything. In a space
  it is on until the owner turns it off; the owner may also switch off a tool or a whole server.
  A space can only narrow what the company allows. Admins see switches only, never what people
  drafted or sent.
- **Every write goes through an MCP server an admin added.** Linear, Notion and GitHub are
  presets pointing at the vendors' own servers; any other server can be added by its address.
  Slack's and Google's hosted servers are gated, so those two are Handbook's own MCP servers
  (`app/mcphost`, `POST /mcp/<name>`) over the regular APIs: Slack posts with the person's user
  token, Google uses `drive.file`, `documents` and `spreadsheets` and writes cells as values.
- **Each person signs in with their own account** (`user_connections`), checked to be the
  company's (Linear's workspace, Google's email, a GitHub repo in a company installation). GitHub
  signs in through the GitHub App, which holds Issues: write only; Handbook's own reading tokens
  stay read-only.
- **One tool loop, paused at each write.** At most 5 cards per request, each approved on its own.
  A write runs only from `confirm`, by the person who proposed it, before it expires (15 minutes),
  with the stored payload; the person may edit a draft's words or ask for changes, never its
  target. Company content is read only through Ask's own retrieval for words quoted from the
  request. Targets and ids come from the asker's words, a lookup in this request, or the tool's
  allowed values; links and @-mentions only if the asker or a source wrote them.
- **Private content** never goes somewhere public; a wider or unknown audience needs a tick on
  the card (`ACTIONS_BLOCK_WIDER=true` refuses instead). A destructive tool is labelled, and its
  card says it cannot be undone.
- **Slack:** a message that asks for an action (a model check whose quote must be in the
  message) or starts with `agent` drafts a card: in a DM as the person, in a channel from that
  channel only as the room, shown only to the asker. Each Slack surface has its own admin switch.
- Every outcome is recorded in `actions`; a person's approved writes are capped at 100 an hour.

Covered by `tests/test_actions_*.py` and `tests/test_mcp_*.py`; benchmarks in `evaluation/`
(`agent_scenarios.py`, `agentbench2/`). Tuned for strong models; smaller models are not yet.

Charts are complete on `main`: PR #45 (open-ended charts) and PR #50–#52 (chart fixes, paragraph
figures, Slack charts) are merged and were walked through on production data on 13 Oct 2026
(Notion and Drive paragraph figures, document tables, Slack, Linear and GitHub activity, Slack
DM and channel charts).

**High-level and list questions in Deep analysis** (PR #53 and #55, merged).
A company-wide question ("what are our revenue streams?") is answered by one sentence in each
of many documents, and a list question ("every weekly update marked At Risk") by every matching
document. Deep read the top three in depth, which filled its budget, so the model saw 2-3
documents; and the answer prompt's "3-5 points" kept about half the facts it was given. Now: a spread-out Deep read takes up to `RAG_DEEP_WIDE_MAX_HITS` (20) documents, one passage
each first, and reads in depth only with the room left; the answer rule `RAG_ASSEMBLE_RULE`
gathers a company-wide, list, count or "which has the most" answer from every block and keeps
every item; and Deep may answer up to `RAG_DEEP_MAX_ANSWER_TOKENS` (2000; normal Ask keeps 700).
Benchmark 3 retest, 13-14 Oct, Claude Haiku, graded by Gemma 4 31B, before -> after on the
same questions: high-level facts 50% -> 70%; list/count questions' facts 53% -> 61%; normal
questions 9/10 both. Four high-level answers are not in the benchmark's documents at all, so
"I don't know" stays the right answer there. Not done: a separate search per part of a
question (the cause of the remaining list misses), held back for its latency and cost.
Opt-in (PR #55): all of the above runs only when the asker turns on **Extra
effort** under Deep analysis (chat `mode: "deep_extra"`). Plain Deep reads the top documents in
depth as before, and normal Ask no longer gets the assemble rule, so neither pays for breadth
it did not ask for.
Extra effort also reads wide on every question (one strong match used to keep the read
narrow: 28 of 61 broad questions read 2-3 documents) and adds documents down to
`RAG_DEEP_WIDE_INCLUDE_RATIO` (0.05) x the best score. Benchmark 4, 16 Oct, Claude Sonnet,
graded by Claude Opus, the 61 broad questions: 69% -> 75% correct, right documents found
74% -> 81%, 11.7k -> 12.9k tokens per question, median time unchanged; none got worse.

## 13. Testing & Verification

- **Automated suite:** 218 test modules and about 2,700 test functions (13 Nov), before
  parametrized cases expand the collection. CI runs `pytest -m "not network and not live_llm"`. The fast
  golden-set tier ran green on PR #44 before merge.
- **Evaluation:** golden-set path-firing in CI; nightly RAGAS scoring; Agent mode scenarios and
  benchmarks, and spreadsheet question sets run by hand (`evaluation/README.md`).
- **Live verification on staging (1 Oct 2026):** Slack private-channel access; Linear public-team
  access; chart access with two users; email change; instant updates from Slack, Linear, Notion
  and Drive with the timings in §7.
- **Live verification (30 Sep 2026):** Drive per-file sharing on a real folder; scheduled report
  email delivery; the frontend walked through by hand.
- **Production verification (team, 1 Oct 2026):** Second Brain (graph, live reads, memory),
  prompt-injection defense, Cloudinary uploads, feedback and gaps, chat history, real email.
- **Not yet verified live:** Google Groups, Google Forms, Linear private teams, each company's own
  Google and Slack app with real apps, identifier masking on staging.

## 14. Current Product Snapshot

- **Implemented:** grounded Ask with routing, follow-ups, summaries, Deep analysis and web
  search; chat history with rename and pin; uploads; charts (activity, document tables, paragraph
  figures, uploads); sheet rows in Ask; spaces; Slack bot; scheduled reports; feedback and gaps;
  Second Brain; per-document access for Drive, Slack and Linear, per-repo for GitHub; instant
  updates for four tools; layered prompt-injection defense; Agent mode through MCP; each
  company's own Google and Slack app; rate and AI limits; identifier masking; self-hosted install
  with Updates.
- **Working in production:** all of the above except what follows.
- **Merged, awaiting a production test:** Linear access rules for private teams; the items
  labelled **Merged** above.
- **Partially supported:** Notion access is per space, not per page; GitHub charts are space-level.
- **Blocked or waiting on an external party:** Notion per-page access (API), Google Groups (admin),
  Linear private teams (Business plan), answer fact-checker (paid host).
- **Deferred:** tuning Agent mode for smaller models, until it is measured.
