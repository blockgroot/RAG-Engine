# Missing Features: what we do not have, and whether we could

| | |
| --- | --- |
| **Subject** | Capabilities a user or buyer would reasonably expect, that this product does not have |
| **Audience** | Whoever is asked "why can't it do X?" and needs an answer that is checked, not guessed |
| **Standing rule** | Every entry carries a **verdict**, a **last-checked date** and its **evidence**. An entry with no evidence is an opinion and does not belong here. |
| **Related documents** | `CLAUDE.md` §7 (engineering to-dos and unverified paths), `docs/ONYX_FEATURE_PARITY_ANALYSIS.md` (what a comparable product has) |

---

## 1. What belongs in this file

Only gaps that a **person outside the codebase** would notice: a feature they expected,
asked for, or saw in a competitor. Internal refactors, deferred optimisations and
test-coverage debt live in `CLAUDE.md` §7 and must not be copied here. Two lists that
overlap will drift, and a gap list nobody trusts is worse than none.

The point of the file is the **verdict**. "We haven't built it" and "it cannot be built"
lead to completely different conversations with a customer, and the expensive mistake is
promising the second one as if it were the first.

## 2. The verdict scale

| Verdict | Meaning |
| --- | --- |
| **BLOCKED** | The upstream service does not expose what we would need. Proven, with the endpoint that does not exist named. No amount of work here changes it. |
| **OPEN** | Proven possible against the provider's own reference or against our code. Not built yet. |
| **UNVERIFIED** | Built, but has never run against the real service. Treat as "does not work" until walked through live. |
| **DECIDED** | Deliberately not built, with the reason. Not a gap, recorded so it is not re-opened by accident. |
| **VERIFIED** | Walked through live against the real service. |
| **DONE** | Built and tested against our own database and the provider's published schema. An entry that also needs a live walkthrough says so. |

**BLOCKED and OPEN are claims, so both require proof.** An unchecked guess is not a
verdict: write `OPEN (unverified)` and the date you failed to check, or check it. Two
verdicts in this file flipped the first time anyone actually read the provider's
documentation (§3.5 and §4.1), and both had been quoted as settled fact for months.

A verdict is only as good as its **last-checked** date. Providers ship APIs. Re-check
before quoting BLOCKED to a customer, and move the date when you do.

---

## 3. Access control

### 3.1 Notion per-page access control

| | |
| --- | --- |
| **Verdict** | **BLOCKED** |
| **Last checked** | 2026-09-29 |

**What is missing.** Drive and Slack carry per-document sharing into retrieval
(`sources.factory.ACL_CAPABLE = {"google", "slack"}`). Notion does not. Every Notion page
indexed into a scope is readable by everyone in that scope, regardless of who the page is
shared with inside Notion.

**Why it matters.** It is the single most likely thing a security-conscious buyer asks
about, because Notion is where most companies keep the sensitive pages (comp bands, board
notes, performance reviews). The answer has to be exact.

**Evidence.**

- The **Page object** has no sharing field. Its complete property list is `object`, `id`,
  `created_time`, `created_by`, `last_edited_time`, `last_edited_by`, `archived`,
  `in_trash`, `icon`, `cover`, `properties`, `parent`, `url`, `public_url`. `public_url`
  says whether a page was published to the web, which is public exposure, not internal
  sharing. https://developers.notion.com/reference/page
- **No permissions endpoint exists** in the public API. The endpoint groups are users,
  blocks, comments, pages, databases, data sources, views, search, file uploads, plus
  auth tokens, meeting notes, custom emojis and agents, none of which reads page
  sharing. No webhook event fires on a permission change either.
  https://developers.notion.com/llms.txt and https://developers.notion.com/reference/webhooks
- **The Admin API does not close it.** Enterprise plan only, organization bot token. Its
  full surface is legal holds, user listing, permission groups, custom agents, MCP
  connections, personal access tokens, workspace exports. The permission-group endpoints
  return workspace groups and their direct user memberships, so they say *who is in a
  group* and never *which group is on which page*, which is the half that produces a
  viewer list. https://developers.notion.com/reference/admin/intro and
  https://developers.notion.com/reference/admin/list-permission-group-members
- **No scope exists for it.** Admin API scopes are `legal-hold`, `managed-user-session`,
  `mcp-client-connection`, `permission-group`, `personal-access-token`, `user`,
  `workflows`, `workspace`. https://developers.notion.com/reference/admin/scopes
- **The teamspace is hidden too.** Teamspace membership is Notion's real access boundary,
  and the API states "Team-level pages are also currently represented as having a
  workspace parent in the API". Parent types are `database_id`, `page_id`,
  `data_source_id`, `block_id`, `agent_id`, `workspace`. So we cannot even tell which teamspace a page is in.
  https://developers.notion.com/reference/parent-object
- **Onyx does not sync Notion permissions either**, which confirms this is the API and not
  our approach. Their permission-syncing list is Confluence, Jira, Google Drive, Gmail,
  Slack, Salesforce, GitHub, Box, Canvas, SharePoint, Microsoft Teams, Outlook.
  https://docs.onyx.app/admins/connectors/overview

**Two routes considered and rejected.**

- *Audit log replay.* The audit log does record "Page permission updated", "Email domain
  permission on page changed" and "Page shared to web". That is a change feed, not a
  state: reconstructing current ACLs means replaying every event since workspace creation
  with no snapshot to anchor against, on an Enterprise-only surface whose docs do not
  describe an API retrieval method. A reconstruction that can silently miss an event fails
  OPEN, which is the one direction access control may never fail.
  https://developers.notion.com/compliance/audit-log-events
- *Unofficial internal API.* `/api/v3/syncRecordValues` does carry permission records, and
  it authenticates with the `token_v2` browser cookie, which grants everything the human's
  own account can reach and must be treated as a password. Undocumented, unsupported, can
  change without notice. Not shippable in a multi-tenant product.

**What we do instead.** Notion's integration share list is itself a boundary: `search`
only returns pages explicitly shared with the integration. A tenant wanting a narrower
Notion corpus connects a separate integration per space and shares only the relevant
pages. That is space-level, not document-level, and the UI must say so.

**What would change the verdict.** Notion shipping a per-page grant read on the public or
Admin API. If a tenant is on Enterprise, the permission-group endpoints become worth
wiring the day that lands, for read-side group expansion in the shape
`app/sources/google_groups.py` already uses. They do nothing on their own.

### 3.2 Linear per-issue access control

| | |
| --- | --- |
| **Verdict** | **DONE**. Public teams verified on staging; the private-team path needs a Business-plan workspace to test live |
| **Last checked** | 2026-09-30 |

**Built** (`app/sources/linear.py::_access_for`, `tests/test_linear_access.py`). Public
team ⇒ readable by the scope; private or restricted team ⇒ its members plus
`Issue.sharedAccess.sharedWithUsers`; unreadable membership ⇒ the connected account
only. Workspace admins and owners are added too, since Linear's own screen says "Private teams
are visible only to team members and workspace admins". Team and sharing ride the issues listing, so revocation re-stamps on the next
sync. Every query validates against Linear's published `schema.graphql`; none has run
against a live workspace yet.

**What is missing.** Linear issues are scope-level. A private team's issues are readable
by everyone in the space that indexed them.

**Why it matters.** Private teams are where a company puts the incident reviews, the
security backlog and anything involving a named employee. We already index them.

**It is possible, and it is the Slack pattern exactly.** A Linear team's membership is its
ACL, the same shape as `sources.slack._access_for`:

- **Membership is readable.** The `teamMemberships` query returns all team memberships in
  the workspace, each carrying its user, its team and an owner flag. `TeamMembership` is a
  first-class type with a `TeamMembershipConnection`.
  https://studio.apollographql.com/public/Linear-API/variant/current/schema/reference
- **Team access IS issue access, stated by Linear.** "Private teams are visible to their
  members only", and issues take their team's access: those who are not a member of a
  private team cannot see its issues. The `teams` query returns "all teams whose issues
  the user can access. This includes public teams and private teams the user is a member
  of." https://linear.app/docs/private-teams
- **The team id is already in hand and emails are already being read.**
  `app/sources/linear.py:55-57` already queries `assignee { id name email }` and
  `team { id key name }` in the single issue query, so `User.email` is proven available
  and the team id needs no new field. Only `teamMemberships` is a new call, and it caches
  per team for the sync the way `slack._channel_meta` caches `conversations.list`.
- **The write side needs nothing new.** Viewers would be team member emails plus a
  `team:<id>` entry, mirroring `slack._channel_entry`, and `ACL_CAPABLE` gains `"linear"`.

**Per-issue sharing is readable, so there is no residual gap.** On 2026-02-13 Linear
shipped sharing individual issues out of private teams (Enterprise). The schema exposes
it: `Issue.sharedAccess { isShared sharedWithUsers }`, plus `inheritsSharedAccess` for a
sub-issue. Those users join the viewer list.
https://linear.app/changelog/2026-02-13-advanced-filters-and-share-issues-in-private-teams
and https://github.com/linear/linear/blob/master/packages/sdk/src/schema.graphql

**Two things the schema shows that the first draft missed.** `Team.visibility` has three
values, not two: `public | private | restricted`, where restricted is "a non-private team
inside a private-team boundary". We treat restricted as members-only, which fails closed
for parent-team members who have not joined. A sub-issue that inherits sharing is trusted
to list the inherited users in its own `sharedWithUsers`; if it does not, they are
withheld (fail closed).

### 3.3 Charts and hover rows stay scope-level

| | |
| --- | --- |
| **Verdict** | **DONE** for document-derived facts. **Not applicable** for GitHub and Forms. |
| **Last checked** | 2026-09-30 |

**Built** (`app/insights/store.py::_viewer_filter`, `tests/test_insights_access.py`).
Counts, hover rows, the subject list a refusal repeats back, and "measured since" all go
through `visibility_predicate`. Linear's issue facts are keyed by identifier, not the
document id, so they join on the issue URL (`documents.source_uri`). One visible
consequence: a fact whose document is gone is hidden, so a deleted Drive file drops out
of chart history.

**What is missing.** Retrieval enforces per-document access; `activity_facts` does not. A
chart counts rows and its hover lists them, with no viewer filter. So a count can include
a document the asker cannot open, and the hover can name its title.

**It is possible for the four indexed providers, verified in our own code.**
`app/insights/facts.py:143` writes `d.source_external_id` into `activity_facts.external_id`
for every `doc_changed` fact. That is exactly the column in the `documents` unique key
`(org_id, source_provider, source_external_id)`, so a fact joins back to its document with
no schema change, and `security/visibility.py::visibility_predicate` can be spliced into
`store.run_metric` and `store.list_facts` the way it is already spliced into the four
retrieval legs. The single spelling rule holds; no new predicate is written.

**It is structurally undefined for GitHub and Forms, and that is not a gap to close.**
GitHub embeds nothing, so no `documents` row exists to filter against; its boundary is the
installation's authorized repos, which is scope-level by construction. Forms responses are
never indexed by design. Saying "charts are unfiltered" without that split overstates the
problem by two providers.

### 3.4 Changing your email revokes every grant

| | |
| --- | --- |
| **Verdict** | **DONE** |
| **Last checked** | 2026-09-30 |

**Built** (`app/auth/email_change.py`, `tests/test_email_change.py`). There was no way to
change an email at all, so an alias list alone would never have been written. `/account`
now requests a change, a single-use link goes to the NEW address (GET shows the page,
POST acts), and the confirmed change keeps the old address in `user_email_aliases`.
`Viewer.acl()` emits aliases beside the primary address, an alias someone else now signs
in with is ignored, and graph identity linking treats aliases as proof too.

**What is missing.** ACL entries are emails by design, because a file is routinely shared
with someone before they sign up. There is no alias list, so when a person's email
changes, every stored grant stops matching and they lose documents they still hold in
Drive. Onyx carries `prior_emails` for this.

**It is possible, and the precedent is already in the class.** `Viewer`
(`app/vectorstore/base.py:35`) already carries `groups` and `channels`, both of which add
entries to the **asker's** side of the `&&` without touching stored rows. An alias list is
that same shape: a table of prior addresses, resolved in `viewer_for_person` (already the
single constructor for an identified viewer) and emitted by `Viewer.acl()`. No re-stamp,
no re-embed, no change to `_VIEWER_SQL`, no upstream dependency at all. Fails closed
today, which is why it is a support ticket and not an incident.

### 3.5 Nested Google Groups are not expanded

| | |
| --- | --- |
| **Verdict** | **DONE** via route 3, needs a live walkthrough (like all group expansion). Previously recorded as BLOCKED; that was wrong. |
| **Last checked** | 2026-09-30 |

**Built** (`app/sources/google_groups.py::_nested_groups`, `tests/test_nested_groups.py`).
After the direct-membership lookup, one `hasMember` per candidate group, in parallel,
where the candidates are this org's indexed `group:` grants in the asker's domain (at
most 40). `hasMember` accepts the `admin.directory.group.readonly` scope we already
request, so nobody reconnects. A failed check withholds that group and is not cached.

**What is missing.** `groups.list?userKey=` returns **direct** memberships only, so a
document shared with a group that contains the asker's group is withheld.

**Three routes exist; the third works on every Workspace edition.**

1. **Cloud Identity `groups.memberships.searchTransitiveGroups`** does exactly what is
   needed: "Search transitive groups of a member", where a transitive group is "any group
   that has a direct or indirect membership to the member". Scopes
   `cloud-identity.groups.readonly` and friends. **Edition-gated**: "This feature is only
   available to Google Workspace Enterprise Standard, Enterprise Plus, and Enterprise for
   Education; and Cloud Identity Premium accounts."
   https://docs.cloud.google.com/identity/docs/reference/rest/v1/groups.memberships/searchTransitiveGroups
2. **Directory `members.list?includeDerivedMembership=true`**, whose parameter is
   documented as "Whether to list indirect memberships. Default: false." This is the
   reverse direction (a group's members), so it needs a known group.
   https://developers.google.com/workspace/admin/directory/reference/rest/v1/members/list
3. **Directory `members.hasMember`** is the one that fits our read path: "Membership can
   be direct or nested, but if nested, the `memberKey` and `groupKey` must be entities in
   the same domain or an `Invalid input` error is returned." No edition gate.
   https://developers.google.com/workspace/admin/directory/reference/rest/v1/members/hasMember

**Why route 3 is the design.** It needs a candidate group list, and we have one for free:
the `group:` entries already stored in `doc_viewers` for that scope are a small, known,
admin-chosen set. So the cost is one bounded `hasMember` per (candidate group, asker),
cached per `(org_id, email)` on the existing 10-minute TTL, and nested grants resolve on
any edition. Route 1 is strictly better where the tenant is on Enterprise.

**What is genuinely closed off.** `groups.list?userKey=` accepts "Email or immutable ID of
the **user**", not a group (its page never says "direct" in so many words; that
reading comes from `members.list` needing an explicit flag for indirect memberships),
so the obvious recursive reverse-walk (list the groups that a
group belongs to) is not available.
https://developers.google.com/workspace/admin/directory/reference/rest/v1/groups/list

---

## 4. Connectors and freshness

### 4.1 No webhooks: syncing is polling only

| | |
| --- | --- |
| **Verdict** | **DONE** for all four, closed until configured and never run live. Drive was previously recorded as BLOCKED; that was wrong. |
| **Last checked** | 2026-09-30 |

**Built** (`app/api/webhooks.py`, `app/sources/drive_watch.py`, `app/api/slack_events.py`,
`tests/test_webhook_sync.py`). A push flags the connection and starts its sync at once (unless it
synced in the last 3 minutes or a sync is already running, in which case the tick picks
it up). So a change usually lands within seconds to a couple of minutes, not after the
1h poll. Each
route answers 404 until its secret is set. What has to be done outside the repo, per
provider, is listed under each one below.

**What is missing.** The flag column and `request_sync()` exist; no webhook endpoint calls
them. Worst-case staleness is one poll interval (1h) rather than one tick.

**Google Drive: the blocker was removed by Google and we never re-checked.** The standing
claim, in `CLAUDE.md` and repeated here, was that Drive can never have a webhook because
Google requires the receiving domain to be verified in Cloud Console and a
`*.onrender.com` host cannot do that. Google's own page now says: *"Domain verification in
the API Console is no longer required to make push notifications work with your domains."*
The remaining requirements are an HTTPS callback URL and a valid, non-self-signed SSL
certificate, both of which `*.onrender.com` satisfies.
https://support.google.com/googleapi/answer/7072069 and
https://developers.google.com/workspace/drive/api/guides/push

  *Operational cost, not a blocker:* channels expire. "If you don't set the `expiration`
  property in your request, the expiration time defaults to 3600 seconds", the maximum is
  "86400 seconds (1 day) ... for the `files` resource and 604800 seconds (1 week) for
  `changes`", and "there's no automatic way to renew a notification channel ... you must
  replace it with a new one by calling the `watch` method." So Drive needs a renewal job
  on the existing tick. A notification arriving at a cold-started free instance is still
  lost, which is exactly what the 1h poll floor is for.

  *Built that way:* one `changes.watch` channel per connection that has a folder, renewed
  a day before its 7-day expiry. Notifications carry no signature, so the channel token
  (stored hashed) is the proof. The change log covers the whole account rather than one
  folder, so any Drive edit flags a sync, which costs one listing diff. Setup:
  `DRIVE_PUSH_BASE_URL`.

**Notion: built.** Setup: create a subscription on the public integration pointing at
`<api>/webhooks/notion`. The server logs the one-time `verification_token`; paste it into
Notion's Verify form and set it as `NOTION_WEBHOOK_VERIFICATION_TOKEN`. The same token
then verifies `X-Notion-Signature`. The docs do not say outright whether a public
integration's subscription receives events from every installing workspace; the payload's
`workspace_id` implies it does. Original finding: "Webhooks let your connection receive real-time updates from Notion.
Whenever a page or database changes, Notion sends a secure HTTP POST request to your
webhook endpoint." Delivered events include `page.content_updated`, `page.locked`,
`comment.created` and `data_source.schema_updated`. Setup is a subscription with a public
SSL endpoint, a one-time `verification_token` round trip, and HMAC-SHA256 payload
signatures, which is the verification shape `slack_events.py` already implements. Plan
availability is not documented. https://developers.notion.com/reference/webhooks

**Linear: no admin scope needed, which corrects the first draft.** The admin-scope rule
("Only workspace admins, or OAuth applications with the `admin` scope, can create or read
webhooks") is about creating webhooks through the API. The same page also says: "OAuth
applications can configure webhook settings. Once those settings are configured, each
time a new organization authorizes the given application, a webhook will be created for
that organization". So it is configured once on our OAuth app, with our `read` scope
unchanged. That no admin scope is needed is inferred from the wording, not stated. One
real limit: these webhooks cover all PUBLIC teams, so private-team issues stay on the
poll. Setup: webhook URL `<api>/webhooks/linear` on the OAuth app, plus
`LINEAR_WEBHOOK_SECRET`. The receiver checks `Linear-Signature` (bare hex HMAC-SHA256 of
the raw body) and a `webhookTimestamp` within 60 s.
https://linear.app/developers/webhooks

**Slack: not just one missing line.** `app/api/slack_events.py` answered EVERY non-DM
`message` event, so subscribing to channel messages would have made the bot reply to all
conversation. A channel message (including edits and deletes) now only flags the
connections whose `channel_ids` hold that channel. Setup: add the bot events
`message.channels` and `message.groups` in the Slack app. Their scopes
(`channels:history`, `groups:history`) are already granted, and the bot only receives
events for channels it is in.

### 4.2 Connector breadth

| | |
| --- | --- |
| **Verdict** | **OPEN** |
| **Last checked** | 2026-09-30 |

Five sources (Notion, Drive, Slack, Linear, GitHub). Onyx ships 40+. Every buyer with
Confluence, Jira, Zendesk, Salesforce or SharePoint is a no today. This is the most common
single reason to lose a deal to a horizontal search product, and it is pure build-out
against documented REST APIs, not a blocker. No upstream check applies.

---

## 5. Built, never run against the real thing

These are not missing code. They are missing **proof**, and until walked through live they
should be described to anyone outside the team as not working.

| Feature | Verdict | Last checked | Status |
| --- | --- | --- | --- |
| Drive per-file permission capture | **VERIFIED** | 2026-09-30 | Walked through live by the team: per-file sharing is enforced on a real folder. |
| Google Groups read-side expansion | **UNVERIFIED** | 2026-09-29 | `GOOGLE_GROUPS_ENABLED` is off, no tenant holds the scope, and the Admin SDK needs a Workspace-admin connection nobody has confirmed exists. |
| Google Forms sentiment | **UNVERIFIED** | 2026-09-29 | The Forms API calls, the `mimeType` listing and the scope behaviour are tested against a fake reader only. Enabling it also forces every tenant to reconnect Google. |
| Scheduler email delivery | **VERIFIED** | 2026-09-30 | Scheduled reports arrive on time at the correct address. |
| The entire frontend | **VERIFIED** by hand | 2026-09-30 | Walked through in the browser by the team. Still no automated rendered test, so a regression is caught by eye or not at all. |
| Prompt-injection guard | **UNVERIFIED** in prod | 2026-09-30 | All four phases built, `GUARD_MODE` unset, so nothing is on. Plan: `GUARD_MODE=shadow` for a week, read `guard.flagged_hit` false positives, then `enforce`. |
| The answer audit (LettuceDetect) | **DECIDED** (on hold) | 2026-09-30 | Paused by decision. Hugging Face CPU Spaces are PRO-only, so the private checker Space cannot be deployed free; the audit stays off. |

---

## 6. Deliberately not built

Recorded so they are not re-opened as oversights. Reasons live at the call sites and in
`CLAUDE.md` §3.

- **No image generation for charts.** A PNG of numbers cannot be re-scoped, filtered or
  clicked through, and a wrong chart reads as a measurement.
- **No spreadsheet charting.** Drive skips Google Sheets. Numbers taken from retrieved
  chunk text are unfalsifiable.
- **No single company sentiment score.** One number invites exactly the management use a
  survey promises not to enable.
- **No image attachments.** There is no vision path at all.
- **No reusable file library (Onyx "Projects").** A file is welded to one conversation.
- **No admin-editable limits UI.** Env vars. A table plus route plus UI for two integers
  is the configuration sprawl the conventions forbid.

---

## 7. Adding an entry

Copy this. An entry without evidence is an opinion; an entry without a date is a
liability, because a BLOCKED verdict quoted a year late is how a customer gets told
something false.

```markdown
### N.N <Feature name>

| | |
| --- | --- |
| **Verdict** | BLOCKED | OPEN | UNVERIFIED | VERIFIED | DECIDED | DONE |
| **Last checked** | YYYY-MM-DD |

**What is missing.** One paragraph, in the words someone outside the codebase would use.

**Why it matters.** Who asks for this and what they cannot do without it.

**Evidence.** Links to the provider's own reference, or file paths in this repo. For
BLOCKED, name the endpoint that does not exist and the page that proves it. For OPEN, name
the endpoint that DOES exist and quote what it returns. Neither verdict is free.

**What would change the verdict.** The specific upstream change, or the work required.
```
