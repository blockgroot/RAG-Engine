# Handbook Rollout Checklist: Syvora

*Version 1.0, 13 November 2026. For Syvora management, the Syvora Admin and the Handbook team.*

This checklist takes Handbook to every Syvora employee, end to end: infrastructure, email, organization and access, Google and Slack apps, content, connecting tools, ingestion and employee onboarding. Each part holds its steps, its checks, and what to do when something goes wrong.

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
| D3  | Hosting              | Run by the Handbook team: always on, with daily database backups. |
| D4  | Sign-in                | Email sign-in links. No passwords.                                                                                                                                                         |
| D5  | Who can join           | Only people an admin invites, only with an `@syvora.com` address. Handbook enforces this.                                                                                                  |
| D6  | Google Drive and Slack | Through apps that **Syvora** owns, created in Parts 6 and 7.                                                                                                                               |
| D7  | Notion, Linear, GitHub | Through Handbook's own apps. Syvora creates no app for these.                                                                                                                              |
| D8  | First ingestion        | **No sensitive content.** The first ingestion covers general content open to all staff (Part 8). Sensitive content is added later inside private spaces.                                   |
| D9  | Private areas          | Private **spaces** can be created for any team that needs its own area, such as HR, each with its own owner and members.                                                                   |
| D10 | Agent mode             | **Off** by default. Admins can turn it on later (Part 5.4).                                                                                                                                |

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

### 3.1 What we use

Syvora's data is kept apart from every other organization by the database itself. Each organization has its own partition of indexed content, protected by row-level security, and every search is limited to the asker's organization before results are ranked.

| Part | Service | What it does |
| --- | --- | --- |
| Web app | Vercel | The pages employees use |
| API and background worker | Render (Singapore) | Answers questions, syncs tools, sends email |
| Database | Supabase PostgreSQL (Mumbai) | Indexed content, who may read each document, chats, settings |
| Files uploaded in chat | Cloudinary | Files employees attach to a chat |
| Email | SendGrid | Sign-in links, invitations, email changes, report notifications |
| AI model | Anthropic Claude | Writes answers and builds charts |
| Background AI model | Google Gemini | Background work: rewriting questions, checking answers, reading documents during ingestion |
| Injection guard | Groq | Checks synced documents and answers for text that tries to instruct the AI; flagged documents are left out of answers |
| Search | Jina | Turns text into vectors and orders results |

**Protections on outgoing text.** Before any text reaches the AI model or the search service, Aadhaar, Virtual ID, PAN, card, mobile, IFSC and UPI values are replaced with placeholders, and restored only in the answer. Bank account numbers are not masked, because no format reliably identifies them; that is why they are Restricted in Part 8. Company data is not used to train models.

### 3.2 Platform readiness

- [ ] The Handbook team confirms to the Syvora Admin that the platform is ready: the service is up, backups are on, and Syvora's usage limits (3.3) are set.

### 3.3 Usage limits

Limits keep any one person, script or company from exhausting the shared service. A refused request is told to try again shortly.

| Limit | Default |
| --- | --- |
| App requests (page loads, lists, refreshes; no AI) | 120 per minute per person; stops scripts, not normal use |
| Questions | 10 per minute per person; 3 per minute per member for the company |
| Sign-in and email-change emails | 20 per minute |
| Answers being written at once | 2 per person; 20 across the service |
| Agent mode writes | 100 per person per hour |
| AI tokens per minute, for each model (Sonnet and Haiku) | Per person: 60k input, 8k output. Per company: 400k input, 40k output |
| AI spend per day, whichever model answers | $0.30 per person; $3.00 per company |

Every answer is counted in tokens against the model that answers, and in dollars against the daily budget. A question is refused once any of these runs out, and allowed again as it refills. Background work is not counted. The Handbook team sets Syvora's limits and watches usage.

### 3.4 If something goes wrong

| What happened | What to do |
| --- | --- |
| Handbook is down | The Handbook team sends an incident notice through the support email (2.1), and another when it is resolved, within 4 working hours |
| People are often told "Too many requests" | The Syvora Admin emails the support address (2.1); the Handbook team reviews Syvora's limits within 1 working day |

---

## 4. Email

### 4.1 Emails Handbook sends

- **Sign-in link**, to the person signing in. Valid for 10 minutes, usable once.
- **Invitation**, to a new person when an admin or space owner invites them.
- **Email-change confirmation**, to the new address when a person changes their sign-in email.
- **Report ready**, to report recipients each time a scheduled report runs.

Email delivery is set up and run by the Handbook team.

### 4.2 Setup

- [ ] So that sign-in emails don't land in spam, the Syvora Admin adds Handbook's sender address as a trusted sender in Google Workspace (**Admin console → Apps → Gmail → Spam, phishing and malware**).

### 4.3 Check

- [ ] Two employees request a sign-in link. Each one arrives in their **inbox**, not spam, within a minute.
- [ ] Each link works only once: clicking it a second time is refused.

### 4.4 If something goes wrong

| What happened | What to do |
| --- | --- |
| Sign-in links not arriving | The Syvora Admin checks Gmail's spam settings, then emails the support address (2.1) |

---

## 5. Organization, people and access

**Owner:** Syvora Admin

### 5.1 Create the Syvora organization

- [ ] **1.** The Syvora Admin opens `https://hand-book.vercel.app`, chooses **Sign up**, and requests the organization **Syvora** with their `@syvora.com` email.
- [ ] **2.** The Handbook team receives the request email and presses **Approve** within 72 hours.
- [ ] **3.** The Syvora Admin signs in with the emailed link. They are the first admin, and `syvora.com` becomes the organization's email domain.
- [ ] **4.** Optionally, under **People**, the Syvora Admin invites a second person and **promotes** them to admin.

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

- [ ] **Actions** (Agent mode): the company switch is **off** (D10).
- [ ] **Model**: shows "Using Handbook's built-in models" (no company model connected). The chat's model picker offers Claude Sonnet 5.5 and Claude Haiku 5.5.
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
| Each release | Read the "What's new" notice that admins see beside the chat modes for 7 days after a release; tell employees about changes that affect them |

---

## 6. Syvora's Google app

**Owner:** Syvora Admin. **Time:** about 10 minutes.

**Why Syvora's own app:** an app marked **Internal** needs no Google review, and only Syvora's Workspace accounts can use it.

**Needed:** a `@syvora.com` Google Workspace account that can create Google Cloud projects.

- [ ] **1.** In Handbook: **Sources → Your company's apps → Google app → Set up**. Keep this page open. It shows every value to copy, each with a **Copy** button.
- [ ] **2.** Open `https://console.cloud.google.com/projectcreate` with the Syvora account. Project name **Handbook** → **Create**. Check the project picker at the top shows **Handbook**.
- [ ] **3.** Enable four APIs in that project (Handbook links to each; check the project is **Handbook**, then press **Enable**):
  - [ ] Google Drive API
  - [ ] Google Docs API
  - [ ] Google Sheets API
  - [ ] Google Forms API
- [ ] **4.** Open **Google Auth Platform → Get started**:
  - App name **Handbook**; user support email: the admin's → **Next**
  - **Audience: Internal** → **Next**
  - Contact email: the admin's → **Next** → accept → **Create**
- [ ] **5.** **Clients → Create client**: application type **Web application**, name **Handbook**. Under **Authorized redirect URIs**, add the **first** address from Handbook, then **Add URI** and the **second** (used by Agent mode). **Create**.
- [ ] **6.** Paste the **Client ID** (ends in `.apps.googleusercontent.com`) and the **Client secret** into Handbook → **Save**. The Google app card shows **Ready**.
- [ ] **7.** Save the client secret in Syvora's password manager as "Handbook – Google app". Nowhere else.

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

- [ ] **1.** In Handbook: **Sources → Your company's apps → Slack app → Set up → Create the Slack app**. Slack opens with the app already filled in.
- [ ] **2.** Choose Syvora's workspace → **Next** → **Create**.
  - If Slack displays an empty field instead, select **Slack shows an empty box instead?** in Handbook to reveal the app template. Copy the template, select the **JSON** tab in Slack, and paste it there.
- [ ] **3.** If Slack says the Request URL did not answer, continue: Handbook does not have the keys yet. Ignore any offer to "run your app locally" and press **Go to App Settings**.
- [ ] **4.** In Slack, **Basic Information → App Credentials**: copy **Client ID**, **Client Secret** and **Signing Secret** (press **Show** for each secret) into the matching boxes in Handbook → **Save**. The Slack app card shows **Ready**.
- [ ] **5.** In Slack, **Event Subscriptions → Retry** next to the Request URL. It shows **Verified**.
- [ ] **6.** Save the three values in Syvora's password manager as "Handbook – Slack app".

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
| **General** | Policies open to all staff, onboarding guides, product and engineering docs, public Slack channels, Linear teams open to all, internal wiki pages | Company-wide, from **Sources** | First ingestion (Part 10) |
| **Confidential** | HR policies in draft, team plans, finance summaries, leadership notes, private project channels | Only inside a private space, connected by its owner | Later, once the space is set up |
| **Restricted** | Salaries, payslips, personal HR files, medical or disciplinary records, ID documents, bank account numbers, customer personal data, contracts with personal data, passwords, API keys, tokens | **Never** | Never |

### 8.2 Scope for the first ingestion

The Syvora Admin fills in this table before Part 9. Only General content.

| Tool | What Handbook will read | How it is chosen | Chosen |
| --- | --- | --- | --- |
| Google Drive | One folder (and everything in it) | Picked in Handbook after sign-in | ☐ |
| Slack | A list of channels | Picked in Handbook after install | ☐ |
| Notion | A list of pages | Picked on Notion's own screen | ☐ |
| Linear | A list of teams | Picked in Handbook | ☐ |
| GitHub | A list of repositories | Picked when installing the GitHub app | ☐ |

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

**Sync finished**

- [ ] In **Sources**, every tool shows its sync as finished, with no error.
- [ ] The notification bell shows no tool with a problem.

**Answers are right**

- [ ] For each tool, ask one question you already know the answer to. Handbook answers correctly and links to the right document.
- [ ] Ask about something that is in none of the tools. Handbook says it found no answer instead of guessing.

**Private content stays private**

- [ ] Someone **without** access to a private Drive file asks about it. Handbook does not answer from that file.
- [ ] Someone **not** in a private Slack channel asks about it. Handbook does not answer from that channel.

**Slack bot works**

- [ ] Mention `@Handbook` in a channel it was invited to, and send it a direct message. It answers both.

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
- [ ] Each member asks real work questions, in the web app and in Slack, and rates the answers (11.2).
- [ ] The Syvora Admin reviews the feedback and the unanswered topics in the notification bell, and asks document owners to fix the missing pages.
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
| Ongoing | Answer questions and problems sent to the support email (2.1) | Handbook team |
| Weekly | Review feedback and unanswered topics; send fixes to document owners | Syvora Admin |
