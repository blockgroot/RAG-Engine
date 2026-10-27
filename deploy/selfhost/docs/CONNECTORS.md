# Connecting your tools

Handbook reads Slack, GitHub, Google Drive, Notion and Linear through **apps that your company registers with each of those services**. Each app points at your Handbook's address, and you keep its keys. Set up only the tools you use; any tool without keys stays off.

For every tool:
1. Create the app (below).
2. Copy its keys into `.env`.
3. Run `docker compose up -d` so Handbook picks up the keys.
4. In Handbook, open **Sources** and connect the tool.

In the steps below, `<domain>` means your `HANDBOOK_DOMAIN`, for example `handbook.yourcompany.com`.

Slack and GitHub apps are created from templates in `apps/`. `install.sh` has already written your domain into them.

---

## Slack

**Create the app from the template**
1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From an app manifest**.
2. Pick your workspace, paste the contents of `apps/slack-manifest.yml`, and press **Create**.
3. Slack may report that the events address did not answer. That is expected, because Handbook does not have the app's keys yet. You fix it in step 6.

**Copy the keys.** Under **Basic Information → App Credentials**:

| Slack shows | Put in `.env` as |
| --- | --- |
| Client ID | `SLACK_CLIENT_ID` |
| Client Secret | `SLACK_CLIENT_SECRET` |
| Signing Secret | `SLACK_SIGNING_SECRET` |

**Finish**
4. Run `docker compose up -d`.
5. In Slack's app settings, open **Event Subscriptions** and press **Retry** next to the Request URL. It should show **Verified**.
6. In Handbook: **Sources → Slack → Connect**, then pick the channels to sync.

**What the template sets**
- Two sign-in addresses: connecting Slack, and Agent mode.
- The events address. Questions come in as @mentions and DMs; channel changes keep synced channels fresh.
- The interactivity address, for Agent mode's approval buttons.
- DMs to the bot turned on.
- **Token rotation off.** Leave it off: Handbook keeps the bot's token, which does not expire.

---

## GitHub

**Create the App from the template**
1. Open `apps/github-app.html` in a browser on your computer.
2. Type your GitHub organization and press **Create on GitHub**. You must be an owner of the organization.
3. GitHub shows the App with everything filled in. Give it a name; it must be unique on all of GitHub, for example "Handbook for Your Company". Then confirm.
4. GitHub sends you to `https://<domain>/?code=...`. Copy the code from the address bar, and **within one hour** run:
   ```sh
   curl -s -X POST https://api.github.com/app-manifests/<code>/conversions
   ```
   GitHub answers with the App's details.

**Copy the keys** from that answer into `.env`:

| GitHub's answer | Put in `.env` as |
| --- | --- |
| `slug` | `GITHUB_APP_SLUG` |
| `client_id` | `GITHUB_CLIENT_ID` |
| `client_secret` | `GITHUB_CLIENT_SECRET` |
| `pem` | `GITHUB_APP_PRIVATE_KEY`, on one line, keeping the `\n` sequences exactly as GitHub returns them |

**Finish**
5. Run `docker compose up -d`.
6. In Handbook: **Sources → GitHub → Connect**, and install the App on the repositories Handbook should read.

**What the template sets**
- Permissions:
  - Metadata, Contents and Pull requests: **read**.
  - Issues: **write**, used only by Agent mode. When Handbook reads, it always uses read-only access, whatever the App holds.
- Two callback addresses, in this order: connecting GitHub, then Agent mode. **Keep that order** if you edit them.
- "Request user authorization during installation" on.
- Webhooks off. Handbook does not use them.

---

## Google Drive

Google has no app template, so this one is set up by hand in [Google Cloud Console](https://console.cloud.google.com).

1. **Project.** Create a project (or pick one) in your company's Google organization.
2. **APIs.** Under **APIs & Services → Library**, enable:
   - **Google Drive API** (always);
   - **Google Docs API** and **Google Sheets API** (only for Agent mode);
   - **Google Forms API** (only with `GOOGLE_FORMS_ENABLED=true`);
   - **Admin SDK API** (only with `GOOGLE_GROUPS_ENABLED=true`).
3. **Consent screen.** Under **OAuth consent screen**, choose **Internal**.
   - Internal means only your company's accounts can sign in, and it skips Google's app review. Drive read access is a "restricted" scope, and External apps need a lengthy review to use it.
4. **Client.** Under **Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - Authorized redirect URIs, add both:
     - `https://<domain>/api/auth/google/callback`
     - `https://<domain>/api/act/mcp/callback` (Agent mode)
5. **Copy the keys:**

| Google shows | Put in `.env` as |
| --- | --- |
| Client ID | `GOOGLE_CLIENT_ID` |
| Client secret | `GOOGLE_CLIENT_SECRET` |

6. Run `docker compose up -d`. In Handbook: **Sources → Google Drive → Connect**, then pick the folder to sync.

**Optional, off by default**
- `GOOGLE_FORMS_ENABLED=true`: reads Google Forms responses.
- `GOOGLE_GROUPS_ENABLED=true`: respects sharing with Google Groups. The person connecting must be a Workspace admin.

After turning one on, connect Google again once so it can ask for the extra permission.

**Live updates.** Handbook asks Drive to notify it of changes at `https://<domain>/api/webhooks/google`. If Google does not deliver them for your domain, Drive is still checked on a schedule.

**Scopes Handbook asks for:**
- **Connecting Drive:** `drive.readonly`, `documents.readonly`.
- **Agent mode:** `openid`, `email`, `drive.file` (only files Handbook creates or is given), `documents`, `spreadsheets`.

---

## Notion

Notion has no app template.

1. **Integration.** Go to [notion.so/profile/integrations](https://www.notion.so/profile/integrations) → **New integration** → type **Public**.
   - Redirect URI: `https://<domain>/api/auth/notion/callback`
   - Capabilities:
     - **Read content**;
     - **Read user information including email addresses** (so Handbook knows who wrote what).
   - Handbook does not need insert or update capabilities.
2. **Copy the keys:**

| Notion shows | Put in `.env` as |
| --- | --- |
| OAuth client ID | `NOTION_CLIENT_ID` |
| OAuth client secret | `NOTION_CLIENT_SECRET` |

3. Run `docker compose up -d`. In Handbook: **Sources → Notion → Connect**, and choose the pages to share on Notion's own screen.

**Live updates (optional, recommended)**
4. In the integration's **Webhooks** tab, add the subscription URL `https://<domain>/api/webhooks/notion` and select the page and database events.
5. Notion sends a verification token to Handbook, which writes it to its log:
   ```sh
   docker compose logs api | grep "notion webhook: verification token"
   ```
6. Paste the token into Notion's **Verify** form, and also into `.env` as `NOTION_WEBHOOK_VERIFICATION_TOKEN`. Then run `docker compose up -d`.

Without live updates, Notion is synced on a schedule.

> Notion has no per-page permission information for Handbook to read. Everything you share with the integration can be found by everyone in your company who uses Handbook.

---

## Linear

Linear has no app template.

1. **OAuth application.** Go to Linear **Settings → API → OAuth applications → New**.
   - Callback URL: `https://<domain>/api/auth/linear/callback`
   - Handbook asks only for **read** access.
2. **Live updates (optional, recommended).** In the same application, turn on webhooks:
   - URL: `https://<domain>/api/webhooks/linear`
   - Events: at least **Issues** and **Comments**.
3. **Copy the keys:**

| Linear shows | Put in `.env` as |
| --- | --- |
| Client ID | `LINEAR_CLIENT_ID` |
| Client secret | `LINEAR_CLIENT_SECRET` |
| Webhook signing secret | `LINEAR_WEBHOOK_SECRET` |

4. Run `docker compose up -d`. In Handbook: **Sources → Linear → Connect**, then pick the teams to sync. Linear's sign-in grants every team, so the choice is made in Handbook.

---

## Agent mode

Agent mode lets people ask Handbook to do things in a tool, such as post a Slack message or create a GitHub issue. Each action is shown on a card and runs only when they approve it.

- **Slack, Google and GitHub:** Agent mode uses the apps above, through the second address you added to each. Nothing more to set up.
- **Notion and Linear:** Agent mode signs people in through Notion's and Linear's own hosted servers. No extra app is needed.

Agent mode is off until an admin turns it on, tool by tool, in **Admin → Actions**.

## If something does not connect

| What you see | Usual cause |
| --- | --- |
| The tool's sign-in page says the redirect address is not allowed | The address in the tool's app does not exactly match `https://<domain>/api/auth/<tool>/callback`. Check for `http` instead of `https`, a missing `/api`, or a trailing `/`. |
| The tool is not offered in Sources | Its keys are missing from `.env`, or `docker compose up -d` has not been run since they were added |
| The Slack bot does not reply | Event Subscriptions is not **Verified** (step 5 under Slack), or Handbook is not reachable from the internet |
| Live updates never arrive | The webhook secret in `.env` does not match the tool's, or Handbook is not reachable from the internet |
