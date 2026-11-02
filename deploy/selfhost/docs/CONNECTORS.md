# Connecting your tools

Handbook reads Slack, GitHub, Google Drive, Notion and Linear through **apps that your company registers with each of those services**. Each app points at your Handbook's address, and you keep its keys. Set up only the tools you use; any tool without keys stays off.

For every tool:
1. Create the app (below).
2. Copy its keys into `.env`.
3. Save `.env`, then run `docker compose up -d` in the Handbook folder so Handbook picks up the keys. Handbook reads `.env` only when it starts: a key saved after that is not seen until you run it again.
4. In Handbook, open **Sources** and connect the tool. Sign in to the tool with the same account (and, for Slack and Linear, the same workspace) you created its app in.

In the steps below, `<domain>` means your `HANDBOOK_DOMAIN`, for example `handbook.yourcompany.com`.

Slack and GitHub apps are created from templates in `apps/`. `install.sh` writes your domain into them each time it runs.

---

## Slack

**Create the app from the template**
1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From an app manifest**.
2. Pick your workspace, paste the contents of `apps/slack-manifest.yml`, and press **Create**.
3. Slack may report that the events address did not answer. That is expected, because Handbook does not have the app's keys yet. You fix it in step 5. Slack may then show **"Run your app locally"** with Slack CLI commands: ignore them (Handbook is the app) and press **Go to App Settings**.

**Copy the keys.** Under **Basic Information → App Credentials**:

| Slack shows | Put in `.env` as |
| --- | --- |
| Client ID | `SLACK_CLIENT_ID` |
| Client Secret | `SLACK_CLIENT_SECRET` |
| Signing Secret | `SLACK_SIGNING_SECRET` |

**Finish**
4. Run `docker compose up -d`.
5. In Slack's app settings, open **Event Subscriptions** and press **Retry** next to the Request URL. It should show **Verified**.
6. In Handbook: **Sources → Slack → Connect**, then pick the channels to sync. If Slack asks which workspace, choose the one you created the app in: the app can only be installed there.
7. To ask the bot in a channel, invite it there (`/invite @Handbook`). The bot answers only people whose **Slack email belongs to a Handbook account**; anyone else is told to ask an admin for an invite (**People → Invite someone**, with the email they use in Slack).

Slack calls Handbook at its public address for events and buttons, so the bot works only when Handbook is reachable from the internet.

**What the template sets**
- Two sign-in addresses: connecting Slack, and Agent mode.
- The events address. Questions come in as @mentions and DMs; channel changes keep synced channels fresh.
- The interactivity address, for Agent mode's approval buttons.
- DMs to the bot turned on.
- **Token rotation off.** Leave it off: Handbook keeps the bot's token, which does not expire.

---

## GitHub

**Create the App from the template**
1. Sign in to Handbook as an admin in your browser.
2. Open `apps/github-app.html` in the same browser.
3. Type your GitHub organization and press **Create on GitHub**. You must be an **owner** of the organization, and type its handle exactly as in its URL (`github.com/<handle>`). To create the App under your personal account instead, leave the field empty.
4. GitHub shows the App with everything filled in. Give it a name; it must be unique on all of GitHub, for example "Handbook for Your Company". Then confirm.
5. GitHub returns you to Handbook, which finishes creating the App and shows **four lines for `.env`** (`GITHUB_APP_SLUG`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`). Press **Copy** and replace the four empty `GITHUB_…=` lines in `.env` with them. The page shows them once: it includes the App's private key.

<details>
<summary>Taking the keys from GitHub instead</summary>

If Handbook's page did not appear, or you created the App by hand, take the values from the App's page in GitHub (**Settings → Developer settings → GitHub Apps → your App**; for an organization, the organization's settings):

| In GitHub | Put in `.env` as |
| --- | --- |
| The App's name in the page's address (`…/settings/apps/<name>`) | `GITHUB_APP_SLUG` |
| **Client ID** | `GITHUB_CLIENT_ID` |
| **Generate a new client secret** (shown once) | `GITHUB_CLIENT_SECRET` |
| **Private keys → Generate a private key** (on newer pages: **Credentials → Key pairs**). It downloads a `.pem` file | `GITHUB_APP_PRIVATE_KEY`, on one line (below) |

The `.pem` file has many lines, and `.env` needs it on one, with `\n` where each line break was. This prints that line; copy it over the empty `GITHUB_APP_PRIVATE_KEY=` line in `.env`:
```sh
echo "GITHUB_APP_PRIVATE_KEY=$(awk 'NF {printf "%s\\n", $0}' ~/Downloads/<your-app>.*.private-key.pem)"
```
It starts `GITHUB_APP_PRIVATE_KEY=-----BEGIN RSA PRIVATE KEY-----\n` and ends `-----END RSA PRIVATE KEY-----\n`, with no quotes. Keep the `.pem` file somewhere safe, or delete it once `.env` is backed up: it is the App's key.

</details>

**Finish**
6. Run `docker compose up -d`.
7. In Handbook: **Sources → GitHub → Connect**, and install the App on the repositories Handbook should read.
8. Each person, admins included, links their own GitHub account in Handbook (**Account → Link GitHub**). A private repository is answered only for people whose linked account can open it; until they link, Handbook answers them "No answer found" about it.

**What the template sets**
- Permissions:
  - Metadata, Contents and Pull requests: **read**.
  - Issues: **write**, used only by Agent mode. When Handbook reads, it always uses read-only access, whatever the App holds.
- Two callback addresses, in this order: connecting GitHub, then Agent mode. **Keep that order** if you edit them.
- "Request user authorization during installation" on.
- No webhook. Handbook does not use GitHub webhooks.

---

## Google Drive

Google has no app template, so this one is set up by hand in [Google Cloud Console](https://console.cloud.google.com).

1. **Project.** Create a project (or pick one) in your company's Google organization.
2. **APIs.** Under **APIs & Services → Library**, search for each of these and press **Enable**. Google refuses any call to an API that is not enabled (*"… API has not been used in project … or it is disabled"*), so enable them now, even if you turn on Agent mode later:
   - **Google Drive API** (always);
   - **Google Docs API** and **Google Sheets API** (Agent mode: creating Docs and Sheets);
   - **Google Forms API** (only with `GOOGLE_FORMS_ENABLED=true`);
   - **Admin SDK API** (only with `GOOGLE_GROUPS_ENABLED=true`).
3. **Consent screen.** Under **OAuth consent screen**, choose **Internal**.
   - Internal means only your company's accounts can sign in, and it skips Google's app review. Drive read access is a "restricted" scope, and External apps need a lengthy review to use it.
   - On newer consoles this screen is **Google Auth Platform → Branding** (the app's name and your email) and **Audience** (Internal or External).
   - **No Google Workspace** (for example a personal Gmail account, or trying Handbook first): Internal is not offered. Choose **External**, leave the app in **Testing**, and under **Audience → Test users** add the email of every account that will connect Drive. A Testing app works for up to 100 test users without Google's review.
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

**If Google refuses the sign-in**

| Google shows | Fix |
| --- | --- |
| **Access blocked: … has not completed the Google verification process** | The app is External and in Testing, and this account is not a test user. Add it under **Audience → Test users** (OAuth consent screen → Test users on older consoles), then connect again. |
| **Access blocked: … can only be used within its organization** | The app is Internal, and this account is outside your Google Workspace. Sign in with a company account. |
| **Error 400: redirect_uri_mismatch** | The redirect URI in the client differs from the one Handbook sent. Connecting Drive uses `https://<domain>/api/auth/google/callback`; connecting Google in **Agent mode** uses `https://<domain>/api/act/mcp/callback`. Both must be listed, exactly: `https`, your domain, no trailing `/`. Google can take a few minutes to apply a change. |
| **Agent mode: "Google Docs API (or Sheets API) has not been used in project … or it is disabled"** | Enable that API (step 2), wait a few minutes, and try again. |
| **"Google hasn't verified this app"** | Expected for a Testing app. Click **Continue** (under **Advanced** if shown). |

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

1. **OAuth application.** Signed in to the Linear workspace you will connect, go to **Settings → API → OAuth applications → New**.
   - Callback URL: `https://<domain>/api/auth/linear/callback`
   - Developer name and Developer URL are only shown to people on Linear's sign-in screen: your company's name and website.
   - Handbook asks only for **read** access.
   - The application belongs to this workspace. Signed in to another one, Linear says *"Could not find OAuth client with clientId …"*: switch workspace (top left in Linear), or turn on **Public** in the application.
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
| The tool is not offered in Sources, or its sign-in says *"… requires …_CLIENT_ID … to be set"* | Its keys are missing from `.env`, or `docker compose up -d` has not been run since they were added |
| `docker compose` says *"required variable HANDBOOK_VERSION is missing a value"* | It was run outside the Handbook folder (or in an old copy of it): `cd` into the folder that holds `.env` and run it again |
| The Slack bot does not reply | Event Subscriptions is not **Verified** (step 5 under Slack), or Handbook is not reachable from the internet |
| The Slack bot replies *"You don't have a Handbook account yet"* | That person's Slack email has no Handbook account: invite that email under **People** |
| Live updates never arrive | The webhook secret in `.env` does not match the tool's, or Handbook is not reachable from the internet |
