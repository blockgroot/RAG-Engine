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
   (runs while drafting, no card) or **Write** (a card), or click **Turn all
   on** (reads as Read, writes as Write, destructive tools left as they are).
2. **Each person signs in** to each server under Account → Agent, with their own
   account. Never a shared key for writes.
3. **Ask in Agent mode.** Reads run while drafting; each write is a card the
   person approves. Same confirm path, fingerprint, expiry and single send as before.

## What each preset adds (`app/actions/mcp_presets.py`)

| Connector | Sign-in | Company check |
|---|---|---|
| Linear | Linear registers Handbook itself | after sign-in: `get_workspace` id is a connected workspace |
| Notion | Notion registers Handbook itself | none yet (Notion's MCP has no workspace id we use) |
| GitHub | Handbook's GitHub App (no self-registration at GitHub) | each write: the repo is in a company installation |
| Slack | Handbook's Slack app | none yet |
| Google Workspace (Drive + Sheets) | Handbook's Google OAuth client, `drive.file` only, one sign-in for both | after sign-in: the Google email is the person's |

**Nothing is blocked (2026-10-22 revision).** Every tool a server lists can be
switched on; the admin decides. A destructive tool (deletes, merges, closes…) is
labelled, never switched on by "Turn all on", asks the admin to confirm, and its
card says it cannot be undone. Every write is still a card the person approves.
What the vendor allows still applies: the GitHub App's permissions (Issues:
write today), Slack's user scopes, Google's `drive.file`.

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
