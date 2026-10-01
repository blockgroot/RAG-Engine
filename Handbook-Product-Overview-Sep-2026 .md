# Handbook — Product Overview (October 2026)

*As of 1 October 2026*

## Summary

Handbook is a multi-tenant AI assistant that answers employees' questions from their own company's tools — Notion, Google Drive, Slack, Linear and GitHub — and refuses rather than guesses when the answer isn't there. It is live in production (Render + Vercel + Supabase) with Ask, charts, scheduled reports, a Slack bot, file uploads, model choice, automatic syncing and the Second Brain all shipped.

- **Who it's for:** companies that want one place to ask "how much leave do I have?", "who reviewed the auth PR?" or "chart commits by author this month" without searching five tools.
- **What makes it different:** answers are grounded in the company's own documents with the source named; access follows each tool's own sharing (Drive per file, Slack per private channel, Linear per private team, GitHub per private repository); numbers in charts come from counted data, never from the model.
- **Where it stands:** in production with real tenants. Since September the Second Brain (knowledge graph, live tool reads, personal memory), prompt-injection protection and real email delivery have gone live, and on 1 October access control was extended to Linear, GitHub and charts, with instant updates from all four indexed tools.
- **What's next:** more flexible charts (in review), then more connectors and a deep research mode.

## Core product

The product is one question box (Ask) over every tool a company connects, organised into the company as a whole and smaller private spaces.

### Ask — grounded answers

- One box for every connected source; Handbook decides which source to read by measuring which one's content best matches the question. Naming a tool ("in Slack…") reads that tool.
- Every answer names where it came from (app, document, who last edited it, when).
- Two safety layers stop invented answers: a confidence threshold refuses before the model is called, and a strict prompt refuses when the retrieved text doesn't actually answer.
- Search combines meaning-based and keyword search, then re-ranks a pool of 16 passages down to the best few.
- Follow-up questions are understood in context ("what about part-timers?"); "summarise everything discussed here" reads the whole channel or space.
- If nothing internal matches and the question is about a real outside entity, one web search is allowed, clearly labelled as web.
- When a document exists but isn't shared with the asker, the answer says so (naming the connector, never the document) instead of a misleading "I don't know".

### Second Brain

- **Knowledge graph:** a map of who worked on what and which documents, issues, pull requests and channels relate, so one answer can span tools ("what is Sana working on across Linear and Slack?"). Every step respects access.
- **Live tool reads:** when a question is about current state ("is SYV-5 still blocked?"), Handbook re-reads that item from the tool instead of trusting the last sync, and withholds it if it was deleted or unshared.
- **Personal memory:** remembers a few facts a person states (team, office, "keep it short"), says so with an Undo, never treats them as a source, and lets people manage them on their account page.

### Connectors

| Connector | How it's read | Who can get answers from it | Instant updates |
| --- | --- | --- | --- |
| Notion | Indexed | Everyone in the space (Notion has no page-sharing API) | Yes |
| Google Drive | Indexed (Docs, PDF, Word) | Only people each file is shared with | Yes |
| Slack | Indexed, chosen channels | Private channels: members only | Yes |
| Linear | Indexed | Private teams: members, admins and people an issue was shared with | Yes, after a one-time reconnect |
| GitHub | Live reads, nothing stored | Private repos: people whose linked GitHub account can open them | Not needed (always live) |
| Google Forms | Sentiment labels only | Admins and space owners; never groups under 5 | n/a |

### Spaces

- A company can create private spaces (e.g. "Meeting notes", "Coding workspace") with their own members and connectors.
- A space sees only its own content, never also the company-wide content, so membership stays meaningful.
- Members land on Ask; owners manage invites and connections.

## Features shipped

| Feature | What it does | Why it matters |
| --- | --- | --- |
| Ask (Q&A) | Ask any question and get an answer from your company's connected tools, with the source shown | Answers come only from your company's documents, and Handbook says so when it doesn't know |
| Second Brain | Connects information across tools, re-reads current state live, and remembers a little about each person | Answers questions no single tool can, and stays current |
| Charts in Ask | "Chart commits by author", "pie of Linear issues by state" as interactive charts in the same box | Every figure is counted from real data, never estimated by AI, and follows access rules |
| Scheduled reports | Daily, weekly or monthly summaries of a connected tool, saved in the app and announced by email | Reports state what was checked and include only what their owner can open |
| Ask in Slack | @mention in a channel or message privately; same answers as the app | Channel answers use only that channel; private documents never reach a shared room |
| File uploads | Attach PDF, Word, CSV, text or JSON files and ask about them alongside company documents | Stored privately in Cloudinary, within that conversation only |
| Model choice | Choose which AI model answers, from a short list of vetted options | Providers are never allowed to train on or keep company data |
| Bring your own model | Use your own AI provider account, 14 providers supported | Only approved providers can be connected |
| Feedback & gaps | Thumbs with reasons; every unanswered question recorded for admins | Admins see which document is missing, and how many people asked |
| Needs-attention bell | Alerts the right person when a tool stops syncing or needs setting up | Alerts go only to people who can act on them |
| Instant updates | Slack, Linear, Notion and Drive notify Handbook on every change; hourly re-check as a safety net | New content searchable in seconds to minutes (staging: Slack ~20 s, Notion ~30 s, Drive ~3 min) |
| Access control | Each tool's own sharing applied to every answer, chart, report and live read | People only ever see what the source tool lets them see |

Also shipped: passwordless sign-in by email (SendGrid) with approval for new companies, changing your sign-in email without losing access, private chat history with shareable links, suggested starter questions for every connected tool, and public product pages.

## Security, privacy and trust

Isolation is enforced in the database query itself at four levels, so no feature can accidentally show one company's or one person's data to another.

| Level | Guarantee | How |
| --- | --- | --- |
| Company | One org never sees another's content | Every table carries the org; every query filters on it before ranking |
| Space | A space sees only its own content | A workspace id nested inside the org on every row |
| Document | A person sees only what the source shared with them | Sharing captured at sync (Drive files, Slack private channels, Linear private teams) and applied in the same query; GitHub private repos checked against the person's linked account; revocations picked up on the next sync |
| Person | Chats, reports, pins, memory and attachments belong to one user | Keyed by org + user; resuming someone else's chat is refused |

- **Fails closed:** if a file's sharing can't be read, only the connected account can see it; if a user's identity can't be resolved, they see public content only; a member without a linked GitHub account sees public repositories only.
- **Never trains or shares:** every model call to OpenRouter forbids data collection.
- **Prompt-injection protection (live):** one written rule for the AI in every prompt, hidden tricks scrubbed from untrusted text, links allowed only if they appeared in the sources, a hidden canary, and a safety model that screens documents and answers. Flagged documents are reported to their owner.
- **Honest coverage:** Notion is per space, not per page, because Notion's API doesn't expose page sharing; every Sources card says who can get answers from that tool.
- **Login:** magic links only, single-use; the session lives in an httpOnly cookie and the org always comes from it, never from the client. Connector tokens are encrypted; webhooks are signature-checked.

## Architecture at a glance

Handbook runs on a deliberately small, mostly free stack: one Python API, one Next.js frontend and one Postgres database, with no Redis or separate job system.

| Layer | Choice |
| --- | --- |
| Backend | Python, FastAPI; LangGraph for routing between source agents |
| Frontend | Next.js 15, plain CSS, hand-drawn SVG charts (no UI kit) |
| Database | Postgres + pgvector on Supabase (Mumbai); also holds the job queue, caches and the knowledge graph |
| Embeddings / re-ranking | BGE-M3 and a cross-encoder, local by default, remote option for deploys |
| LLM | Provider chosen from a fixed list (Gemini by default), OpenRouter/Groq for model choice |
| Safety model | Groq-hosted safeguard model for prompt-injection screening |
| File storage | Cloudinary, private assets with expiring links |
| Hosting | Render (Singapore); an external tick every 10 minutes runs syncs; webhooks trigger instant ones |
| Email | SendGrid |

Question → Route to the best-matching source → Hybrid search + re-rank (+ knowledge graph) → Confident match? → **Yes:** grounded answer + sources / **No:** refuse or explain access

Every question passes the same filters for company, space and document access before search results are ranked.

## Blockers and known gaps

| Area | Status | What unblocks it |
| --- | --- | --- |
| Notion per-page access | Blocked: Notion's API has no page-sharing information | Notion adding it; workaround is one Notion connection per space |
| Google Groups access | Built, off: needs a Google Workspace admin to connect Google (a non-admin connection breaks the Google reconnect) | A tenant with Workspace-admin rights |
| Linear private teams | Built, not tested live: private teams are a Linear Business-plan feature | A Business-plan workspace |
| GitHub per-person access | Built, to be tested in production; members must link GitHub | A production walkthrough |
| Google Forms sentiment | Built, not tested: no real survey data yet | A real form with responses |
| Answer fact-checker | Built, on hold: hosting needs a paid plan | A paid host |

**Production setup still to finish (after the 1 October release):** webhook secrets and URLs for Linear and Notion, the Drive push address, Slack's channel-message events, and **each company reconnecting Linear once** to receive instant updates.

### Infrastructure limits

- **LLM rate limit:** free Gemini is 15 requests/min shared by live questions and background work; a separate background endpoint is the fix.
- **Hosting:** Render free sleeps after 15 min idle (kept awake by the external tick) and the always-on service uses ~730 of 750 free hours/month. GitHub disables the tick workflow after 60 days of repo inactivity.
- **Scale:** one Postgres holds documents, vectors, queue, caches and the graph. Fine for early tenants; not the architecture for thousands of companies.

## Roadmap

### In progress

**More flexible charts (PR #45, built, in review):** ask for any combination ("pull requests merged per repository, split by person"), chart extra fields the tools already return (such as labels), and chart tables inside documents (such as a sales table in a Doc). The AI still never produces a number.

### Next

| Item | Scope |
| --- | --- |
| More connectors | Confluence and Jira first, then Zendesk, Salesforce, SharePoint |
| Deep research mode | Multi-step research reports using the graph, search and live tools |
| Action tools | Create issues or post updates, with explicit confirmation |
| Cache tier | Redis/Valkey for hot, disposable data, scoped per company, space and access |
| AI extraction | Model-extracted relationships for the graph, on a separate background budget |
| MCP server | Let Claude Desktop/Cursor users query Handbook with per-user sign-in |

### Scaling path

Postgres stays the source of truth, partitioned by company as tenants grow; vectors can move to a dedicated store behind the existing interface; Redis takes cache load; the queue can move off Postgres when volume demands. Every table already carries the company id, so this is an incremental move, not a rewrite.
