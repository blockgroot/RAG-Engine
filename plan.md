# Plan: Act mode (write actions)

> **Status: PROPOSED, not built.** The detailed design (tables, flow, provider notes, sources)
> is in [docs/plans/2026-10-13-write-actions.md](docs/plans/2026-10-13-write-actions.md).
> This page is the plain-language version.

## What it is

A new **Act mode**, separate from Ask and Chart. In Act you tell the assistant to *do*
something in your tools: file a Linear issue, comment on a GitHub issue, add to a Notion
page, create a Google Doc, post in Slack. It drafts the action, shows you exactly what it
will do, and does it **as you** only after you click Approve.

**Separate on purpose:**
- It has its own toggle, its own backend route (`POST /act/stream`), its own package
  (`app/actions/`) and its own tests.
- Ask and Chart never import it. If Act breaks, Ask and Chart keep working.
- It is off by default. An org that doesn't want it never sees it.

## How we act as the user (no passwords, ever)

Each person connects their own account once, through the tool's own OAuth page. It's the
same "Connect" flow admins use today, but done by the individual and asking only for
write permissions.

1. The first time you ask Act to file a Linear issue, it says *"Connect your Linear account
   so I can do this as you."*
2. Linear's own page, where you're already logged in, asks *"Allow Handbook to create
   issues and comments for you?"*
3. You click Allow, and Linear gives us a token tied to **you**, limited to what you allowed.
   We store it encrypted, keyed by (org, user, provider).

That is delegated access you granted and can revoke at any time, from the tool's settings or ours.
The plumbing already exists: "Link your GitHub account" records which person started the
flow (`oauth_states.user_id`). It just throws the token away today.

| | Org / workspace connection (exists) | Personal connection (new) |
|---|---|---|
| Who connects | Admin / space owner | Each person, once per tool |
| Used for | Reading and syncing | Writing only |
| Rights | What the admin can see | Only what that person can do |

**Why not let the bot write with the org token?** Every write would then carry the admin's
or bot's full power, and one bug in our own check would let anyone write anywhere. With a
personal token, Linear, GitHub, Notion or Google itself refuses when you lack the right.

| Tool | Writes as | Permissions requested |
|---|---|---|
| Linear | You | Create issues, create comments (never `write`/`admin`) |
| GitHub | You (with the App badge) | Issues: write only, and each write gets a token GitHub narrows to that one repo. **No `Contents: write` (no push, branch or merge) and no `Pull requests: write` (no PR approve, edit or close)**. Handbook's own reading tokens are made read-only |
| Notion | You for comments; pages likely show "Handbook Actions" (to be tested) | A **separate** "Handbook Actions" connection that can only add content and comments. The existing read connection stays read-only, because a Notion token always carries everything its connection can do |
| Google | You | `drive.file` only: new docs, plus files you pick. No paid yearly security assessment (CASA) |
| Slack | The org's bot, saying "on behalf of @you" | Existing `chat:write`. Slack forbids apps posting as a person. Posts only where both the bot and you are members |

## Guardrails: the model never acts on its own

The model never holds a token and never calls an API. It can only fill in a form;
everything after that is ordinary code.

1. **A fixed menu** of about 10 actions. There is no "call any API" tool.
2. **Impossible, not just forbidden:**
   - No push to `main` or any branch, no merge: the GitHub App has no permission to write code, so GitHub itself refuses.
   - No deletes, closing, sharing changes or invites: not on the menu, and not in the scopes.
3. **Only on an explicit request:**
   - It runs only in Act mode.
   - The model must quote the words in your message that asked for the write, and code checks that the quote is really there.
   - It never offers a write you didn't ask for.
4. **You choose the target, not the documents.** The team, repo, channel or page comes from your
   words or a picker. A retrieved document saying "post this to #general" is ignored.
5. **Nothing runs until you approve.** You see the exact content and destination. The server
   runs that exact stored copy (checked by a fingerprint of the content), exactly once.
   The proposal expires after 15 minutes.
6. **One action per approval.** A write never triggers another write.
7. **Never unattended.** No scheduled or background writes.
8. **Limits and off switches:** a per-person hourly cap, an admin toggle per action, and a server kill switch.
9. **Everything is logged:** who asked, what was sent, where, the decision and the result.
   A timeout is marked "unknown" and never retried automatically, so there are no duplicates.

## Private content reaching a wider audience

Approval alone doesn't stop this. Priya can read a private HR doc, asks Act to "post a summary of the
reorg doc to #general", and approves it, because it's what she asked for. So every proposal
records which documents the draft used and compares their viewers with the destination's audience:

| Destination audience | Result |
|---|---|
| Only people who can already see every private source | Normal card |
| Some people who can't see a private source, or audience unknown (Notion, private GitHub repo) | Card names the docs and how many people are newly exposed. **Approve stays disabled until you tick** "I understand this shares private content" |
| Public (public Slack channel, public GitHub repo) | **Blocked** |

Warn-with-tick, not block, for the middle row: turning a private thread into an issue for the same
team is the main legitimate use, and the team rarely matches the thread exactly. An org can turn
the middle row into a block too (`ACTIONS_BLOCK_WIDER`).

## What a user sees

1. Switch to **Act** and type: *"Create a Linear issue in Platform about the login timeout
   from yesterday's #eng thread."*
2. First time only: *"Connect your Linear account."* One click.
3. The assistant reads the thread (read-only, as today) and drafts the issue.
4. A card appears showing the team, title, body and "will be created as you", with **[Approve] [Cancel]**.
5. Approve, and the issue is created and linked. Cancel or 15 minutes passing, and nothing happens.

## What admins control

- Act mode is off until an org admin turns on individual actions.
- A workspace owner can switch actions off for their space.
- Admin › Actions lists every action taken, with a filter by person.

## Act from Slack

Ask in plain words: `@Handbook create a Linear issue in Platform about this thread`, or the same
in a DM with the bot. A small check decides whether you asked for something to be done (the words
that asked must really be in your message); a question is answered exactly as before. Starting with
`act` forces a draft. Either way it is only a draft until you click Approve.

| | DM with the bot | Private channel | Public channel |
|---|---|---|---|
| What the draft can use | Company-wide, with your access | Only that channel's own messages, as the room | Same |
| Who sees the draft | You (it's your DM) | Only you (a private card) | Only you |
| Posting to Slack | Any channel you and the bot are both in | Only a reply in that thread | Same |
| Admin switch | "Allow Act from Slack DMs" | "Allow Act from Slack channels" | Same |

Only the person who asked can approve. Private content never goes to a public channel.

## Order of work

| Step | What ships | Visible? |
|---|---|---|
| 0 | Tables (`user_connections`, `action_policies`, `actions`), the Act package skeleton, logging | No |
| 1 | Act mode in web chat: **Linear** (issue, comment), **GitHub** (issue, comment) | Yes, for orgs that enable it |
| 2 | **Notion** (comment, add to page), **Google** (create a new Doc) | Yes |
| 3 | **Slack**: posting (as the bot, naming you), and acting from Slack DMs and channels in plain words with Approve buttons | Yes (built) |
| 4 | Later, each decided separately: editing existing content, changing issue status, undo | Not yet |

v1 only creates new things and adds comments. It never edits, deletes or touches code.

## To decide before step 1

1. Can workspace owners switch Act on for their space if the org left it off, or only switch it off?
2. Should GitHub writes go in the existing App (every installation must re-approve the new
   permissions) or a separate opt-in App?
3. How long do we keep each person's action history (their own drafts and results)? Admins see no log.
4. Is a Slack Marketplace listing planned? It affects step 3.
