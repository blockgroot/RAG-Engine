# Checklist After Installing Handbook

Handbook is installed and you can sign in as its admin. This checklist takes you from there to your colleagues using it. Work through it in order; most steps take a few minutes. If we installed Handbook for you, also complete section 8.

## 1. Check the installation

- [ ] `https://<your domain>` opens with a valid certificate, and you can sign in.
- [ ] `docker compose ps` shows all five services running, and `api` as `healthy`.
- [ ] A sign-in link reached your inbox, not spam. If it went to spam, ask your mail team to allow the sending address.
- [ ] A daily backup is scheduled, and the backups are copied off the server ([BACKUP.md](BACKUP.md)).
- [ ] A copy of `.env` is kept somewhere safe, apart from the backups.

## 2. Connect your tools

For each tool your company uses, follow [CONNECTORS.md](CONNECTORS.md), then connect it from **Admin › Connections**.

- [ ] **Slack:** pick the channels Handbook reads. To use the bot in a channel, invite it there (`/invite @Handbook`). It can only read and answer in channels it has been added to.
- [ ] **Google Drive:** pick the folder Handbook reads. Connect with an account that can open everything in that folder.
- [ ] **Notion:** choose the pages to share with Handbook when Notion asks. Pages you do not share are not read.
- [ ] **Linear:** pick the teams Handbook reads.
- [ ] **GitHub:** install the app on the repositories Handbook may read.
- [ ] Each tool's first sync has finished. The bell at the top shows any tool that failed or still needs setting up.

Only connect what people should be able to ask about. Handbook follows each tool's own sharing where the tool reports it: a Drive file, a private Slack channel, a private Linear team or a private GitHub repository is answered only for people who can open it there. **Notion does not report who can see a page,** so everything you share with Handbook from Notion can be found by everyone in the company (or the space it is connected to).

## 3. Try it yourself

- [ ] Ask a few questions whose answers you know, one for each connected tool, and check that each answer cites the right document.
- [ ] Ask something that is not in your tools, and check that Handbook says it could not find an answer rather than guessing.
- [ ] In Slack, mention the bot in a channel it was invited to, and send it a direct message.

## 4. Decide who sees what

- [ ] **Company-wide or spaces.** Tools connected in **Admin › Connections** are for the whole company. For a team that needs its own private area (HR, leadership, a project), create a **space**: it has its own members and its own connected tools, and sees only its own content.
- [ ] **Space owners.** Make one person the owner of each space. Owners invite members and connect the space's tools.

## 5. Choose your settings

- [ ] **AI model:** check the model Handbook answers with (**Admin › Model**). It uses your Claude key by default.
- [ ] **Agent mode** (Handbook carrying out actions in your tools, such as posting a Slack message or creating an issue): off until you turn it on. Every tool action starts off, and each one you turn on is either read-only or needs the person to approve a card before it runs (**Admin › Actions**). Leave it off if you are not ready for it.

## 6. Invite your colleagues

There is no public sign-up page: people join when an admin invites them.

- [ ] Invite colleagues from **Admin › Members**. Each gets an email with a sign-in link. Start with a small group if you prefer.
- [ ] Make at least one more person an admin, so you are not the only one who can manage Handbook.
- [ ] Ask people with access to private GitHub repositories to link their GitHub account from **Account**, so Handbook can answer about those repositories for them.
- [ ] Tell people where Handbook is, and that they can also ask in Slack.

## 7. Keep it running

| How often | What |
| --- | --- |
| Weekly | Look at the bell. It shows tools that stopped syncing, and topics several people could not get answered, which point to documents that are missing or out of date. |
| Each new version | Read the release notes and run `./upgrade.sh` ([UPGRADE.md](UPGRADE.md)). |
| When someone leaves | Remove them in **Admin › Members**. Their access ends at once. |
| Monthly | Check that recent backups exist and that the disk is not filling up (`df -h`). |

## 8. If we installed Handbook for you: remove our access

Once setup is finished, use this section to make sure we can no longer reach your server, your tools or your data. Skip it if your own team installed Handbook.

If you keep us on a support plan, keep only the access that plan names, and remove everything else here.

### 8.1 The server

- [ ] Remove the SSH key(s) we used from `~/.ssh/authorized_keys` for every user we logged in as.
- [ ] Delete any user account created for us on the server.
- [ ] Remove any firewall or VPN rule opened for us, such as SSH from our IP address.
- [ ] If Handbook runs in your cloud account (AWS, Google Cloud, Azure), delete the users, roles or access keys you gave us, and check your cloud's audit log for anything else created under them.

### 8.2 Your tools

During setup we may have been given admin rights to create Handbook's apps. In each tool you connected:

- [ ] **Slack:** remove us from the workspace or downgrade us from admin, and remove us as a collaborator on the Handbook app (app settings, **Collaborators**).
- [ ] **GitHub:** remove us from the organization, and from the Handbook GitHub App's managers if we were added.
- [ ] **Google:** remove us from the Google Cloud project that holds Handbook's sign-in client (**IAM**), and from any Workspace admin role.
- [ ] **Notion:** remove us from the workspace, or from the integration's owners.
- [ ] **Linear:** remove us from the workspace, or downgrade us from admin.

### 8.3 Handbook itself

- [ ] On Handbook's admin page, remove any account we used, so only your people remain.

### 8.4 Secrets we may have seen

We do not keep copies of your `.env` or your keys. If your security policy says to change any secret someone outside your company has seen, change these. After editing `.env`, run `docker compose up -d`.

| Secret | How to change it | What happens |
| --- | --- | --- |
| `AUTH_JWT_SECRET` | Set a new value: `openssl rand -hex 32` | Everyone is signed out and signs in again. Nothing else changes. |
| Connector app secrets (`SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_CLIENT_SECRET`, `GOOGLE_CLIENT_SECRET`, `NOTION_CLIENT_SECRET`, `LINEAR_CLIENT_SECRET`, `LINEAR_WEBHOOK_SECRET`) | Regenerate each in the tool's app settings and put the new value in `.env` | Connected tools keep working |
| `LLM_API_KEY`, `EMBEDDING_API_KEY`, `CLOUDINARY_API_SECRET` (if set) | Create a new key with the provider, put it in `.env`, then delete the old key there | Nothing, once the new key is in place |
| `EMAIL_SMTP_PASSWORD` | Change it on your mail server and in `.env` | Nothing |
| `POSTGRES_PASSWORD` | Usually not needed: the database cannot be reached from outside the server. Ask us for the steps if your policy requires it. | |
| `AUTH_ENCRYPTION_KEYS` | Usually not needed. Changing it means every tool has to be connected again in Handbook. | |

If you entered the API keys and passwords yourself during setup, we never saw them, and only `AUTH_JWT_SECRET` and the connector app secrets apply.

### 8.5 Confirm

- [ ] Check the server's login history (`last`) and your cloud's audit log for any access from us after the date you removed it.
- [ ] Keep the image registry token we gave you: Handbook needs it to download upgrades. It only lets you download our images, not reach anything of yours.
- [ ] Tell us it is done, so we can close our record of the setup.
