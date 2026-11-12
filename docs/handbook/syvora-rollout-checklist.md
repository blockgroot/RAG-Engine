# Handbook Rollout Checklist: Syvora

*Version 1.0, 11 November 2026. For Syvora management, the Syvora Admin and the Handbook team.*

This checklist takes Handbook to every Syvora employee, end to end: infrastructure, email, organization and access, Google and Slack apps, content, connecting tools, ingestion and employee onboarding. Each part holds its setup, its checks, how it is run afterwards, and what to do when something goes wrong.

**How to use it:** work through the parts in order. A part is finished when every box in it is ticked.

---

## Contents

1. Decisions
2. Roles
3. Infrastructure
4. Email
5. Organization, people and access
6. Syvora's Google app
7. Syvora's Slack app
8. Content
9. Connecting the tools
10. Ingestion
11. Employee onboarding

---

## 1. Decisions

Every decision the rollout depends on, made in advance. To change one, change it here first and record the date.

| #   | Topic                  | Decision                                                                                                                                                                                   |
| --- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| D1  | Deployment             | **Handbook Cloud**, the production service the Handbook team runs. Syvora is one organization in it. Syvora installs nothing; it creates its Google and Slack apps and connects its tools. |
| D2  | Web address            | `https://hand-book.vercel.app`                                                                                                                                                             |
| D3  | Hosting plans          | Render **Starter** (always on) for the API, Render Key Value **Starter**, and Supabase **Pro** with daily backups.                                                                         |
| D4  | Sign-in                | Email sign-in links. No passwords.                                                                                                                                                         |
| D5  | Who can join           | Only people an admin invites, only with an `@syvora.com` address. Handbook enforces this.                                                                                                  |
| D6  | Email                  | SendGrid, sending from Handbook Cloud's configured sender address.                                                                                                                         |
| D7  | Google Drive and Slack | Through apps that **Syvora** owns, created in Parts 6 and 7.                                                                                                                               |
| D8  | Notion, Linear, GitHub | Through Handbook's own apps. Syvora creates no app for these.                                                                                                                              |
| D9  | First ingestion        | **No sensitive content.** The first ingestion covers general content open to all staff (Part 8). Sensitive content is added later inside private spaces.                                   |
| D10 | Private areas          | Private **spaces** can be created for any team that needs its own area, such as HR, each with its own owner and members.                                                                   |
| D11 | AI model               | Claude, as configured in production.                                                                                                                                                       |
| D12 | Agent mode             | **Off** by default. Admins can turn it on later (Part 5.4).                                                                                                                                |

---

## 2. Roles

Two parties run the rollout.

| Who | Responsible for |
| --- | --- |
| **Handbook team** | Hosting, database, backups and uptime; email delivery; approving the organization; product fixes and new versions |
| **Syvora Admin** | Everything on Syvora's side: the Google and Slack apps, choosing and connecting content, people and spaces, onboarding employees, and reporting faults to the Handbook team |

The Syvora Admin can promote a second person to admin under **People**, so administration never depends on one person. Where a step needs rights in another tool (for example, a Google Workspace admin account or permission to install Slack apps), the Syvora Admin uses an account that has them or asks the person in Syvora who does.

### 2.1 Support and incident communication

Handbook has **one official support email:** `hbook1809@gmail.com`, run by the Handbook team. It is used in both directions:

- **To the Handbook team:** employees and the Syvora Admin send every question, problem or suspected incident to this address.
- **From the Handbook team:** every incident notice (what happened, what is affected, when it is resolved) is sent from this address to the Syvora Admin, who passes it on to employees where needed.

The Handbook team replies within 1 working day, and within 4 working hours when Handbook is down.

---

## 3. Infrastructure

**Owner:** Handbook team

### 3.1 What we use and where

Syvora's data is kept apart from every other organization by the database itself. Each organization has its own partition of indexed content, protected by row-level security, and every search is limited to the asker's organization before results are ranked.

| Part                      | Service                                     | Where                                       | What it does                                                                                                    |
| ------------------------- | ------------------------------------------- | ------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Web app                   | Vercel                                      | `https://hand-book.vercel.app`              | The pages employees use. Requests to `/api` are passed to the API.                                              |
| API and background worker | Render, service **Hand-Book**               | Singapore; `https://hand-book.onrender.com` | Answers questions, syncs tools, sends email. Settings live in the Render dashboard.                             |
| Database                  | Supabase PostgreSQL with pgvector           | Mumbai (`ap-south-1`)                       | Indexed content, who may read each document, chats, settings. Connected through the session pooler (port 5432). |
| Rate limits               | Render Key Value (Valkey 8)                 | Render                                      | Request limits only. Holds no company content.                                                                  |
| Files uploaded in chat    | Cloudinary                                  | Private, authenticated assets               | Files employees attach to a chat.                                                                               |
| Email                     | SendGrid                                    | Over HTTPS                                  | Sign-in links, invitations, email changes, report notifications.                                                |
| AI model                  | Anthropic Claude                            | Anthropic API                               | Writes answers and builds charts.                                                                               |
| Search                    | Jina (`jina-embeddings-v3`, plus reranking) | Jina API                                    | Turns text into vectors and orders results.                                                                     |
| Scheduler                 | GitHub Actions (`tick` workflow)            | Every 10 minutes                            | Starts the hourly re-sync and clean-up jobs.                                                                    |

**Protections on outgoing text.** Before any text reaches the AI model or the search service, Aadhaar, Virtual ID, PAN, card, mobile, IFSC and UPI values are replaced with placeholders, and restored only in the answer. Bank account numbers are not masked, because no format reliably identifies them; that is why they are Restricted in Part 8. Company data is not used to train models.

### 3.2 Platform readiness

- [ ] Render service **Hand-Book** is on the **Starter** plan (Render → Hand-Book → Settings → Instance type).
- [ ] Render Key Value is on **Starter**, and `REDIS_URL` is set on Hand-Book.
- [ ] Supabase is on **Pro** with daily backups on. `DATABASE_URL` uses the session pooler (`*.pooler.supabase.com`, port 5432), not the transaction pooler.
- [ ] Production changes are made **only in the Render dashboard**.
- [ ] On Render, `FRONTEND_URL` is exactly `https://hand-book.vercel.app`. Every sign-in return address is built from it.
- [ ] On Vercel, `NEXT_PUBLIC_API_BASE_URL` is not pointed at Render.
- [ ] `https://hand-book.onrender.com/health` returns 200.
- [ ] `https://hand-book.vercel.app` opens with a valid certificate.
- [ ] The latest `tick` run in GitHub Actions succeeded within the last 20 minutes.
- [ ] `OWNER_NOTIFICATION_EMAIL` on Render is the Handbook team's address, so Syvora's organization request (Part 5.1) reaches them.
- [ ] `MASK_IDENTIFIERS` is not set to `false`, and the prompt-injection guard (`GUARD_MODE`) is on.
- [ ] `RATE_LIMIT_ENABLED` is not set to `false`, and `REDIS_URL` is set, so the limits in 3.3 hold across every server.
- [ ] `LLM_MAX_RPM` on Render matches the requests-per-minute limit of the Anthropic account's tier. The built-in default (15) is sized for a free tier and is too low for a company.
- [ ] `CHAT_MAX_CONCURRENT` (default 20) is no higher than the Anthropic and Jina plans can serve at once.
- [ ] `AUTH_ENCRYPTION_KEYS` is stored in the Handbook team's password manager. Without it, every saved tool connection is unreadable after a database restore.

**Done when:** health returns 200, the tick is green and the plans are upgraded.

### 3.3 Rate limits and AI usage limits

Limits keep any one person, script or company from exhausting the shared service. A refused request is told to try again shortly.

| Limit | Default |
| --- | --- |
| API calls | 120 per minute per person |
| Questions | 10 per minute per person; 3 per minute per member for the company |
| Sign-in and email-change emails | 20 per minute |
| Answers being written at once | 2 per person; 20 across the service |
| Agent mode writes | 100 per person per hour |

**AI model:** calls to Claude are paced to `LLM_MAX_RPM`, with room always kept for people's questions. Each call has a reply-length cap. There is no per-company token budget; the Handbook team watches usage in the Anthropic console and changes limits on Render.

### 3.4 Running the platform

| How often | Task |
| --- | --- |
| Weekly | Check the `tick` workflow is green and Render shows no repeated errors |
| Monthly | Check Supabase backups exist and the database size fits the plan |
| Monthly | Check Anthropic and Jina usage against their plans, and Render logs for repeated "Too many requests" refusals |
| Every 60 days | Make at least one commit to the repository; GitHub turns off scheduled workflows after 60 days without activity |
| Each release | Publish release notes; admins receive them as a notification |

### 3.5 If something goes wrong

| What happened | First action | Who, how fast |
| --- | --- | --- |
| People are often told "Too many requests" | Check which limit refuses in the Render logs; raise it on Render, or upgrade the Anthropic or Jina plan | Handbook team, within 1 working day |
| Handbook is down | Check `/health`, Render and Supabase status; send an incident notice through the support channel (2.1), and another when it is resolved | Handbook team, within 4 working hours |

---

## 4. Email

**Owner:** Handbook team

### 4.1 Emails Handbook sends

- **Sign-in link**, to the person signing in. Valid for 10 minutes, usable once.
- **Invitation**, to a new person when an admin or space owner invites them.
- **Organization request**, to `OWNER_NOTIFICATION_EMAIL` when someone requests a new organization (Part 5.1). The approve link is valid for 72 hours.
- **Email-change confirmation**, to the new address when a person changes their sign-in email.
- **Report ready**, to report recipients each time a scheduled report runs.

### 4.2 Setup

- [ ] On Render: `EMAIL_SENDER=sendgrid`, `EMAIL_SENDGRID_API_KEY` set, and `EMAIL_SMTP_FROM` set to Handbook Cloud's sender address, whose domain is verified in SendGrid. *(Handbook team)*
- [ ] In Google Workspace Admin → Apps → Gmail → Spam, phishing and malware, Handbook Cloud's sender address is an approved sender. *(Syvora Admin)*
- [ ] The SendGrid API key is kept only in Render and the Handbook team's password manager. It is never written in a document, ticket or chat. *(Handbook team)*

### 4.3 Check

- [ ] Two Syvora employees each get a sign-in link in their **inbox** (not spam) within 1 minute.
- [ ] A link used a second time is refused.

### 4.4 If something goes wrong

| What happened | First action | Who, how fast |
| --- | --- | --- |
| Sign-in links not arriving | Check the SendGrid activity for that address, then Gmail's spam settings | Handbook team and Syvora Admin |
| The SendGrid key was exposed | Create a new key in SendGrid, update it on Render, delete the old key | Handbook team, same day |

---

## 5. Organization, people and access

**Owner:** Syvora Admin

### 5.1 Create the Syvora organization

1. [ ] The Syvora Admin opens `https://hand-book.vercel.app`, chooses **Sign up**, and requests the organization **Syvora** with their `@syvora.com` email.
2. [ ] The Handbook team receives the request email and presses **Approve** within 72 hours.
3. [ ] The Syvora Admin signs in with the emailed link. They are the first admin, and `syvora.com` becomes the organization's email domain.
4. [ ] Optionally, under **People**, the Syvora Admin invites a second person and **promotes** them to admin.

**Check**

- [ ] The admin can sign in.
- [ ] An invitation to an address outside `syvora.com` is refused.

### 5.2 Access rules Handbook enforces

- Every search is limited to Syvora's organization and to what the asker may read, before results are ranked. The same rule covers answers, charts, reports and suggestions.
- A **space** is a private area with its own members and its own connected tools. It sees only its own content. A non-member is refused.
- Chats are private to the person who wrote them. No answer is reused for anyone else.
- Admins can invite, promote, demote and remove members. Removing a member ends their access at once. The last admin cannot be demoted.

### 5.3 Spaces

A space can be created for any team that needs its own private area, such as HR. For each space:

- [ ] The team's lead creates the space under **Spaces** and becomes its owner. (Any member can create a space; an owner can make another member owner with **Make owner**.)
- [ ] The owner invites the team as members.
- [ ] The owner connects the team's content inside the space (Part 8 decides what may be connected).
- [ ] A non-member asks about the space's content and gets no answer from it.

### 5.4 Settings

- [ ] **Actions** (Agent mode): the company switch is **off** (D12).
- [ ] **Model**: shows the production Claude model.
- [ ] Scheduled reports: any member can set one up when they need it.

**Turning on Agent mode later**

- [ ] Turn on the company switch under **Actions**.
- [ ] Turn on tools one at a time: **read** tools first, then the chosen **write** tools.
- [ ] Tell employees: every write is shown as a card and runs only when they approve it, as themselves, with their own sign-in to that tool. A card expires after 15 minutes.

### 5.5 Joiners, leavers and changes

**Joiner** (Syvora Admin)

- [ ] On their first day, invite them under **People** with their `@syvora.com` address, the same one they use in Slack.
- [ ] Add them to the spaces their role needs.

**Leaver** (Syvora Admin)

- [ ] If they are an admin, promote a replacement first.
- [ ] If they own a space, make another member of that space its owner first (**Make owner**).
- [ ] On their last day, **Remove** them under **People**. Their access ends at once, and they are removed from every space.

**Email change** (the employee)

- [ ] They change it under **Account**. The new address must be `@syvora.com`. The old one is kept, so files shared with it still match.

### 5.6 Running the organization

| How often | Task |
| --- | --- |
| Monthly | Review who is an admin and who owns each space |
| Each release | Read the release notification; tell employees about changes that affect them |

---

## 6. Syvora's Google app

**Owner:** Syvora Admin. **Time:** about 10 minutes.

**Why Syvora's own app:** an app marked **Internal** needs no Google review, and only Syvora's Workspace accounts can use it.

**Needed:** a `@syvora.com` Google Workspace account that can create Google Cloud projects.

1. [ ] In Handbook: **Sources → Your company's apps → Google app → Set up**. Keep this page open. It shows every value to copy, each with a **Copy** button.
2. [ ] Open `https://console.cloud.google.com/projectcreate` with the Syvora account. Project name **Handbook** → **Create**. Check the project picker at the top shows **Handbook**.
3. [ ] Enable four APIs in that project (Handbook links to each; check the project is **Handbook**, then press **Enable**):
   - [ ] Google Drive API
   - [ ] Google Docs API
   - [ ] Google Sheets API
   - [ ] Google Forms API
4. [ ] Open **Google Auth Platform → Get started**:
   - App name **Handbook**; user support email: the admin's → **Next**
   - **Audience: Internal** → **Next**
   - Contact email: the admin's → **Next** → accept → **Create**
5. [ ] **Clients → Create client**: application type **Web application**, name **Handbook**. Under **Authorized redirect URIs**, add the **first** address from Handbook, then **Add URI** and the **second** (used by Agent mode). **Create**.
6. [ ] Paste the **Client ID** (ends in `.apps.googleusercontent.com`) and the **Client secret** into Handbook → **Save**. The Google app card shows **Ready**.
7. [ ] Save the client secret in Syvora's password manager as "Handbook – Google app". Nowhere else.

**If something goes wrong**

| You see | Do this |
| --- | --- |
| "That isn't a Google Client ID" | Paste the Client ID, not the secret. |
| Error 400: redirect_uri_mismatch | Copy both addresses again from Handbook. Google can take up to 5 minutes to apply a change. |
| **Internal** is greyed out | The account is not in Syvora's Workspace. Use a Workspace admin account. |
| "Google Docs API has not been used in project…" | Enable that API (step 3), wait a few minutes, retry. |
| Lost the secret | **Clients → Handbook → Add secret**, then paste the new one with **Change keys** and press **Reconnect** on the Google Drive card. |
| The secret was exposed | Same as a lost secret, the same day, then delete the old secret in Google. |

---

## 7. Syvora's Slack app

**Owner:** Syvora Admin. **Time:** about 5 minutes.

**Why Syvora's own app:** Slack limits apps shared between companies to about one page of messages a minute. Syvora's own app reads at full speed.

**Needed:** permission to install apps in Syvora's Slack workspace (workspace admin or owner).

1. [ ] In Handbook: **Sources → Your company's apps → Slack app → Set up → Create the Slack app**. Slack opens with the app already filled in.
2. [ ] Choose Syvora's workspace → **Next** → **Create**.
   - If Slack displays an empty field instead, select **Slack shows an empty box instead?** in Handbook to reveal the app template. Copy the template, select the **JSON** tab in Slack, and paste it there.
3. [ ] If Slack says the Request URL did not answer, continue: Handbook does not have the keys yet. Ignore any offer to "run your app locally" and press **Go to App Settings**.
4. [ ] In Slack, **Basic Information → App Credentials**: copy **Client ID**, **Client Secret** and **Signing Secret** (press **Show** for each secret) into the matching boxes in Handbook → **Save**. The Slack app card shows **Ready**.
5. [ ] In Slack, **Event Subscriptions → Retry** next to the Request URL. It shows **Verified**.
6. [ ] Save the three values in Syvora's password manager as "Handbook – Slack app".

**If something goes wrong**

| You see | Do this |
| --- | --- |
| "That isn't a Slack Signing Secret" | The Signing Secret is the third value under App Credentials. |
| Retry still fails | Check the Slack app card in Handbook says **Ready**, then retry. |
| Syvora's workspace isn't offered when installing | The app installs only in the workspace it was created in. Create it again in Syvora's workspace. |
| A secret was exposed | Regenerate it in Slack the same day, save it in Handbook with **Change keys**, and press **Reconnect** on the Slack card. |

---

## 8. Content

**Owner:** Syvora Admin.

### 8.1 What may be connected

| Class | Examples | Where it goes | When |
| --- | --- | --- | --- |
| **General** | Policies open to all staff, onboarding guides, product and engineering docs, public Slack channels, Linear teams open to all, internal wiki pages | Company-wide, from **Sources** | First ingestion (Part 9) |
| **Confidential** | HR policies in draft, team plans, finance summaries, leadership notes, private project channels | Only inside a private space, connected by its owner | Later, once the space is set up |
| **Restricted** | Salaries, payslips, personal HR files, medical or disciplinary records, ID documents, bank account numbers, customer personal data, contracts with personal data, passwords, API keys, tokens | **Never** | Never |

### 8.2 Scope for the first ingestion

The Syvora Admin fills in this table before Part 9. Only General content.

| Tool | What Handbook will read | How it is chosen | Chosen |
| --- | --- | --- | --- |
| Google Drive | One folder (and everything in it) | Picked in Handbook after sign-in | [ ] |
| Slack | A list of channels | Picked in Handbook after install | [ ] |
| Notion | A list of pages | Picked on Notion's own screen | [ ] |
| Linear | A list of teams | Picked in Handbook | [ ] |
| GitHub | A list of repositories | Picked when installing the GitHub app | [ ] |

### 8.3 Clean-up before connecting

- [ ] Open the chosen folder, channels, pages, teams and repositories and move out anything Confidential or Restricted.
- [ ] **The Syvora Admin**, in each tool, searches the chosen content for `password`, `token`, `secret` and `api key`, moves anything found to Syvora's password manager, and deletes it from the tool. Handbook does not detect or remove passwords or keys itself, so this is done before connecting.
- [ ] In Drive, files are checked for sharing set to "anyone at Syvora": those are readable by every employee through Handbook.
- [ ] Every chosen Notion page is fine for **every** employee to read. Notion gives Handbook no per-page permissions, so anything shared from Notion can be found by everyone in the organization.

### 8.4 Adding a source, folder or space later

- [ ] Classify the content (8.1). Restricted: stop.
- [ ] **The Syvora Admin** searches it in the tool for passwords, keys and tokens, moves them to Syvora's password manager and deletes them from the tool before connecting.
- [ ] Confidential: connect it only inside a private space.
- [ ] Notion: confirm every selected page is fine for everyone where it is connected.
- [ ] After the first sync, ask one known question and check the citation.
- [ ] Add it to the scope table in 8.2.

### 8.5 Keeping content in scope

| How often | Task | Owner |
| --- | --- | --- |
| Quarterly | Re-check every tool's scope against 8.1, including every shared Notion page | Syvora Admin |

---

## 9. Connecting the tools

**Owner:** Syvora Admin, from **Sources**. Connect in this order.

### 9.1 Google Drive

- [ ] On the **Google Drive** card, press **Connect**.
- [ ] Sign in with a Syvora account that can open everything in the chosen folder.
- [ ] Pick the folder from 8.2. The first sync starts once the folder is saved.

### 9.2 Slack

- [ ] On the **Slack** card, press **Connect** and approve the app for Syvora's workspace.
- [ ] Before picking, type `/invite @Handbook` in each **private** channel from 8.2. Handbook joins public channels itself, but can read a private one only after it is invited.
- [ ] Pick the channels from 8.2. The first sync starts once the channels are saved.
- [ ] In each channel where employees may ask the bot, type `/invite @Handbook`.

### 9.3 Notion

- [ ] On the **Notion** card, press **Connect**.
- [ ] On Notion's screen, select **only** the pages from 8.2.

### 9.4 Linear

- [ ] On the **Linear** card, press **Connect** and sign in to Syvora's Linear workspace.
- [ ] Linear's sign-in grants every team, so pick the teams from 8.2 **in Handbook**.

### 9.5 GitHub

- [ ] On the **GitHub** card, press **Connect**.
- [ ] Install the app on **Only select repositories** and choose those from 8.2. Never "All repositories".
- [ ] GitHub content is read live when someone asks. Nothing from it is turned into search passages; only activity counts (commits, pull requests, issues) are stored, for charts.

---

## 10. Ingestion

### 10.1 How it works

1. **Start.** Each tool's first sync starts as soon as its folder, channels or teams are saved.
2. **Read.** Handbook lists the tool's content and records **who may read each document**, taken from the tool itself.
3. **Prepare.** Each new or changed document is turned into text, split into passages of about 256 tokens (never more than 4,000 characters), and each passage is turned into a vector for search.
4. **Store.** Passages are stored in Syvora's partition with the document's reader list. Tables inside documents are also stored as rows, so charts can count them.
5. **Stay current.**
   - Slack, Linear, Notion and Drive tell Handbook when something changes. Measured times: Slack searchable in about 20 seconds, Notion about 30 seconds, Drive about 3 minutes.
   - Many changes at once cause at most one sync every 3 minutes.
   - Every tool is also re-checked every hour. Only changed documents are processed again.
6. **Removing.** Sharing removed in a tool is applied at the next sync. **Disconnecting a tool deletes everything indexed from it.**

Slack is read for the **last 90 days** of each chosen channel, then kept current. Messages shorter than 15 characters with no replies, and the bot's own messages, are not indexed.

### 10.2 Who sees what after ingestion

| Tool | Who gets answers from it |
| --- | --- |
| Google Drive | People each file is shared with. If sharing can't be read, only the account that connected Drive. |
| Slack | Public channels: everyone. Private channels: their members only. |
| Linear | Teams open to all: everyone. Private teams: their members. |
| GitHub | Public repositories: everyone. Private repositories: only people whose linked GitHub account can open them. |
| Notion | Everyone in the organization (or in the space where it is connected). |
| Files shared with a Google Group | Not opened through the group. Only people the file is shared with directly. |

### 10.3 Check the first ingestion

**Owner:** Syvora Admin

- [ ] Every tool's card in **Sources** shows its sync finished, with no error.
- [ ] The bell at the top shows no tool that failed or needs setting up.
- [ ] One question per tool, whose answer is known, is answered correctly and **cites the right document**.
- [ ] One question about something not in any tool gets "no answer found", not a guess.
- [ ] Someone who cannot open a chosen private Drive file asks about it, and gets no answer from it.
- [ ] Someone who is not in a chosen private Slack channel asks about it, and gets no answer from it.
- [ ] In Slack, `@Handbook` in a channel it was invited to, and a direct message to it, both answer.

### 10.4 Keeping ingestion healthy

| What happened | First action | Who, how fast |
| --- | --- | --- |
| A tool stopped syncing | The bell shows it. Press **Reconnect** on its card. | Syvora Admin |
| Restricted content was ingested | Remove it from the tool or from the chosen scope, then sync; disconnect if urgent | Syvora Admin, within 1 hour |
| Someone saw content they should not | Disconnect that tool in **Sources** (this deletes its indexed content), then investigate | Syvora Admin, within 1 hour |

The Syvora Admin checks the bell regularly for tools that stopped syncing.

---

## 11. Employee onboarding

**Owner:** Syvora Admin

### 11.1 First group

- [ ] Choose a first group of employees from several teams, together covering every connected tool.
- [ ] Invite them under **People** with the `@syvora.com` email **they use in Slack** (the bot answers only people whose Slack email has a Handbook account).
- [ ] Email them the steps in 11.2 (how to sign in, ask, and get help).
- [ ] Members with private GitHub repositories link GitHub under **Account → Link GitHub**.
- [ ] Each member asks real work questions, in the web app and in Slack, and rates every answer with thumbs up or down.
- [ ] The Syvora Admin reviews thumbs-down reasons and the documentation gaps in the notification bell, and asks document owners to fix the missing pages.
- [ ] When the first group is working well, the Syvora Admin invites every employee under **People** and emails them the steps in 11.2.

### 11.2 What each employee does

1. Open the invitation email and click the sign-in link. It works for 10 minutes; if it expires, request a new one on the sign-in page.
2. Ask a question in **Ask**. Each answer shows its sources.
3. If you work on private GitHub repositories, link GitHub under **Account → Link GitHub**.
4. In Slack, mention `@Handbook` in a channel it is in, or send it a direct message.
5. Rate answers with thumbs up or down. Topics Handbook could not answer reach the admins.
6. Do not upload Restricted files (8.1) to a chat.
7. For any question or problem, email `hbook1809@gmail.com`.

### 11.3 Feedback and help

| How often | Task | Owner |
| --- | --- | --- |
| Ongoing | Answer questions and problems sent to the support channel (2.1) | Handbook team |
| Weekly | Review thumbs-down and documentation gaps; send fixes to document owners | Syvora Admin |
