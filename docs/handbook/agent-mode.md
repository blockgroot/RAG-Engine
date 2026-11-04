# Agent mode

Agent mode lets people ask Handbook to **do** things in their work tools, not just answer questions. Examples:

- "Create a Linear issue in BEN summarising what was discussed in #hb-testing today."
- "Post the link of the issue we created in #hb-testing."
- "Create a Google Sheet of the open issues in BEN."

Handbook reads what it needs, drafts the change, and shows it as a **card**. **Nothing is written until the person approves the card.**

It works in the web chat (turn on the **Agent** chip) and in Slack bot dm and mentions in the channel.

---

## How it works

1. **You ask in your own words.** Handbook acts only when the message clearly asks for an action. A question is answered as a question.
2. **Handbook gathers what it needs.** It reads company content (a doc, a Slack channel, a thread) and looks up records in the tools (an issue, a team, a repository).
3. **It drafts each action as a card.** One request can make up to five cards. Each card shows what it will do and where.
4. **You decide each card.** **Approve**, **Edit** the text, **Ask for changes** in words, or **Cancel**. A card expires after 15 minutes.
5. **It continues with the real result.** After you approve, the next step uses what actually happened. For example, the Slack post gets the real link of the issue that was just created. Cancelling stops the request.

Each write runs as **you**, with your own sign-in to that tool. It never uses a shared or admin account.

---

## What it can do today

Agent mode connects to each tool through an **MCP server** (a standard way for an AI to use a tool's actions). Every tool on a server starts **off**. An admin turns each one on, as **read** (used while drafting, no card) or **write** (drafted as a card), to match what the company wants people to be able to do. Admins can also turn everything on at once.

### The tool's own MCP server

| Tool | Tools available | Notes |
|---|---|---|
| **Linear** | 69 (37 read, 32 write) | Linear's official MCP server: issues, comments, projects, cycles, documents, labels and more. |
| **GitHub** | 46 (25 read, 21 write) | GitHub's official MCP server. Handbook's GitHub App allows writing **issues only**, so GitHub itself refuses code, pull requests and merges, whatever the admin turns on. |
| **Notion** | All tools on Notion's server | Notion's official MCP server: pages, databases and comments. |

### Handbook's own MCP server

| Tool | Tools available |
|---|---|
| **Slack** | 5: list channels, find a person, read a thread, post a message, send a direct message |
| **Google** (Docs, Sheets, Drive) | 15: search and read files; create a doc or a sheet; add rows, update cells, add a tab; add to or replace text in a doc; rename, copy, move, create a folder, comment, share |

**Why Slack and Google run on Handbook's own server.** Both companies host an official MCP server, but neither is open to an app like Handbook:

- **Slack's** server only accepts a company's internal apps or apps listed in the Slack Marketplace.
- **Google's** server (Drive, Sheets) is in a Developer Preview, open only to projects Google admits.

So Handbook runs its own MCP server for these two, built on Slack's and Google's regular, public APIs. It works the same way as the others: every tool is off until an admin turns it on, and each write runs as the person, with their own sign-in. Slack messages are sent from the person's own Slack account, never the bot. Google uses the narrowest access: Handbook can only open files it created or that the person opened through it.

### Across tools

- Several actions in one request, such as "an issue in Linear and in GitHub".
- Chains where step two uses step one's result: create an issue, then post its link.
- "The issue we created" works for cards approved in the last 12 hours.

---

## Safety built in

- **Approval for every write.** What runs is exactly the card you approved.
- **No surprise targets.** The team, issue, channel or person must come from your request or from a real lookup, never from text inside a document. Instructions hidden in a document can't redirect a write.
- **No invented links.** A link appears only if you, a source, or an approved card actually gave it.
- **Private content needs your tick.** If a draft uses content only some people can see, you must confirm before it can go somewhere others might read it.
- **Company accounts only.** Sign-ins are checked against the company's own workspace or domain.
- **Admins stay in control.** Admins switch Agent mode on or off for the company and for each Slack surface. Each tool's actions can be off, read-only, or allowed to write. Space owners can switch tools off for their space. Admins see the switches, never what people drafted.

---

## Checks before a card is shown

Handbook checks every draft before you see it:

- **Is everything you asked for on the card?** If a summary, list or link is missing, the draft is sent back to be completed. If it still can't be completed, the card says plainly what it couldn't include.
- **Was the data actually fetched?** Content that has to come from a tool (a channel, a list of issues) must have been read or looked up first. A card can't be drafted from nothing.
- **Does the data match what was fetched?** Rows and lists on a card must come from what was read, or from your own words. A column header alone doesn't count as data, and a list cut short with "..." is sent back to be written in full.

---

## Which model to use

Agent mode works best with a **strong model, such as Claude**, and that's what it's tuned for.

Companies can bring their own model. When an admin adds one, Handbook tests whether it can use tools, which Agent mode needs. **Admin › Model** then shows one of:

- **Works:** the model calls tools properly.
- **Works with a fallback:** the model writes tool calls as text, and Handbook reads them.
- **Not available:** the model can't use tools. Agent mode tells people to pick another model.

---

## Known gaps

Smaller or open models (for example GPT-OSS 120B) follow instructions less reliably, and Agent mode isn't optimised for them yet. With these models you may see:

- **A reply instead of a card.** The model answers ("Here is the link…") instead of drafting the action.
- **Giving up early.** "I couldn't find the issue" when it exists, or asking "which BEN?" instead of looking it up.
- **Wrong search filters.** The model filters by a value the tool doesn't have (an issue state called "open"), finds nothing, and stops. Handbook now runs that search again without the guessed value, so the model gets the real results.
- **Incomplete drafts.** A sheet without rows, a list that stops with "...", or a message without the pointers you asked for. Handbook sends these back and says what's missing, but it can't always make a weaker model fill them in. A list that is simply shorter than it should be, with no "...", can't be caught.
- **Slower requests.** A small model may take 10 or more steps (25–40 seconds) for a request a strong model does in a few.

In every case the safety rules still hold: nothing is written without your approval, and an incomplete card says so rather than looking finished.

Other limits, whatever the model:

- **Google files:** Handbook can only open files it created or that you opened through it. Editing an existing sheet made elsewhere needs that file shared through Handbook first.
- **GitHub:** issues only. No code, pull requests or merges.
- **Slack:** one person per direct message. No scheduled messages or reactions yet.
- **Not yet supported:** Google Calendar events and Gmail drafts.
- **Five actions per request, and cards expire after 15 minutes.**
- **Earlier chat messages aren't used.** Each Agent request stands on its own, apart from the cards you approved in the last 12 hours.
