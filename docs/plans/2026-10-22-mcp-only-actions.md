# Agent mode through MCP only

> **Status (2026-10-22): built, local.** Replaces the hand-written Linear,
> GitHub, Notion, Google and Slack actions (removed) and extends
> [2026-10-21-mcp-actions.md](2026-10-21-mcp-actions.md).

**Decision:** every Agent-mode write goes through the tool's own MCP server.
The five connectors are presets an admin adds in one click; any other tool is
added by its MCP address. Handbook's guardrails sit on top of every tool.

**Why:** the agent benchmark against Onyx (2026-10-21, `evaluation/agentbench/`)
showed the built-in actions could not READ the issue a request named (A16, A17),
while MCP read tools could; and every new operation needed new code. Onyx's
unsafe writes (closing every issue, filing in a team it chose, guessing an
assignee) came from having no approval and no grounding, not from MCP. Those
guards are Handbook's and stay.

## How it works

1. **Admin › Actions → Tools.** Add a connector (Linear, Notion, GitHub, Slack,
   Google Drive, Google Sheets) or any server by address. Sign in to it yourself
   under Account → Agent, then **Refresh tools**. Set each tool to Off, **Read**
   (runs while drafting, no card) or **Write** (a card), or click **Turn on
   recommended** (reads as Read, allowed writes as Write).
2. **Each person signs in** to each server under Account → Agent, with their own
   account. Never a shared key for writes.
3. **Ask in Agent mode.** Reads run while drafting; each write is a card the
   person approves. Same confirm path, fingerprint, expiry and single send as before.

## What each preset adds (`app/actions/mcp_presets.py`)

| Connector | Sign-in | May ever be turned on | Company check |
|---|---|---|---|
| Linear | Linear registers Handbook itself | reads, `save_issue`, `save_comment`, `create_issue_label` | after sign-in: `get_workspace` id is a connected workspace |
| Notion | Notion registers Handbook itself | search, fetch, create, update, comment | none yet (Notion's MCP has no workspace id we use) |
| GitHub | Handbook's GitHub App (no self-registration at GitHub) | issues, labels, comments, reads; never code, PRs, branches, repos | each write: the repo is in a company installation |
| Slack | Handbook's Slack app | search, read, send/schedule message | none yet |
| Google Drive / Sheets | Handbook's Google OAuth client, `drive.file` only | create a file, read; Sheets values, never formulas | after sign-in: the Google email is the person's |

GitHub signs in through the GitHub App, which holds **Issues: write** only, so
even a tool an admin turned on by mistake cannot push or merge: GitHub refuses it.

## Setup outside Handbook

- **GitHub App:** add `<FRONTEND_URL>/api/act/mcp/callback` to its callback URLs.
- **Slack app:** add the same redirect URL; Slack's MCP accepts only approved
  apps (internal or Marketplace) and the workspace admin must allow it.
- **Google:** the Drive/Sheets MCP servers are in Developer Preview: the Cloud
  project must be accepted, the MCP APIs enabled, and the redirect URL added to
  the OAuth client.
- `FRONTEND_URL` (or `MCP_REDIRECT_URI`) set on the backend.

## What was removed

`app/actions/{linear,github,notion,drive,slack}.py`, the per-tool connect flows
(`/act/connect/*`), the Google folder picker, the Notion "Actions" connection,
Slack's personal user-scope sign-in, and their settings. Old per-person tokens and
per-action switches are deleted once (`schema.sql`, mark `act_mcp_only`).

## Known gaps

- **Audience is unknown for every MCP write**, so a draft using private content
  always asks for the tick (the built-ins knew a Linear team's or Slack channel's
  members). Per-preset audience checks can come back on top.
- **Notion and Slack have no company-account check yet.**
- **Lost built-in niceties:** Slack DMs sent as the person with "Sent with
  Handbook", Drive folder choice, Linear's team/assignee resolution messages.
- **Slack and Google depend on the vendor:** Slack's app approval, Google's preview.
- **Re-run the agent benchmark** with Linear and GitHub presets before relying on it.
