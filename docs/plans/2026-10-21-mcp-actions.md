# Other tools through MCP

> **Status (2026-10-21): built, local.** Admin › Actions → "Other tools (MCP)";
> Account → Agent lists each server to sign in to.

**Goal:** Agent mode can write into any tool that publishes an MCP server
(Asana, Jira, HubSpot…) without a connector per tool, under the same approval
flow as Linear, GitHub, Notion, Drive and Slack.

**In one line:** MCP is only the pipe for the last step. The model fills in a
form, code checks it, the person approves a card, and only then is the tool
called, once, with the person's own sign-in, from the stored copy.

## Flow

1. **Admin adds a server** (name + `https://` address). Handbook checks the
   address is public, asks the server how people sign in (protected-resource
   and authorization-server metadata), and registers itself there (dynamic
   client registration). A server with no sign-in lists its tools at once; one
   with sign-in lists them after the admin signs in and clicks Refresh tools.
2. **Every tool starts Off.** The admin sets each one to:
   - **Write**: drafted and shown on a card, sent only on approval.
   - **Lookup**: a read Handbook may run while drafting, with the person's
     sign-in, no card. Only for tools the server also marks read-only.
   - Tools whose name or hints say delete, merge, close, move, rename or edit
     code are **Blocked**: no mode can be set.
   The choice is stored with a hash of the tool's name, description and input
   schema. If the server changes the tool, it is Off again until re-chosen.
3. **Each person signs in** under Account → Agent (OAuth with PKCE, the
   `resource` parameter binding the token to that server). Never a shared login.
4. **A request** offers the model the person's Write tools (as forms) and
   Lookup tools. Lookups run (at most `MCP_MAX_LOOKUPS`), their answers are
   fenced as untrusted data, and the model asks again. Each write is then
   checked and shown as a card: Edit, Ask for changes, Approve, Cancel.
5. **Approve** calls the tool once, with the stored words. A send with no
   answer is `unknown`, never retried. The result's link becomes "Open in …".

## Guards an unknown tool keeps

| Guard | How |
|---|---|
| Nothing runs without approval | Same `confirm` path: stored payload, fingerprint, expiry, one claim |
| A target or setting is never invented | Every value that is not drafted text must be in the asker's words, in a lookup's answer in this request, or one of the schema's own allowed values |
| Links and mentions | Drafted text is cleaned like every draft |
| Who sees it is unknown | `Audience(members=None)`: private content needs the tick; what a lookup returned counts as a private source |
| Tool descriptions are outside text | Scrubbed and cut to 300 characters; the admin read them before allowing the tool |
| Server-side changes | Schema hash: a changed tool is Off |
| No access into our network | Every address (server, metadata, endpoints) must be public `https://` |
| Admin control | Server switch, per-tool mode, Remove (forgets everyone's sign-in) |

## Not done (yet)

- A space cannot narrow single MCP tools; a space owner can still turn Agent mode off for the space.
- Servers without dynamic client registration (an admin-supplied client id) are refused.
- Old SSE-transport servers are not supported; Streamable HTTP only.

## Config

`MCP_REDIRECT_URI` (default `FRONTEND_URL` + `/api/act/mcp/callback`),
`MCP_TIMEOUT_SECONDS` (20), `MCP_MAX_LOOKUPS` (4).
