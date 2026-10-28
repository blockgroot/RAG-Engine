# Removing Our Access After Setup

If we installed Handbook for you, use this checklist once setup is finished to make sure we can no longer reach your server, your tools or your data. Skip it if your own team installed Handbook.

If you keep us on a support plan, keep only the access that plan names, and remove everything else here.

## 1. The server

- [ ] Remove the SSH key(s) we used from `~/.ssh/authorized_keys` for every user we logged in as.
- [ ] Delete any user account created for us on the server.
- [ ] Remove any firewall or VPN rule opened for us, such as SSH from our IP address.
- [ ] If Handbook runs in your cloud account (AWS, Google Cloud, Azure), delete the users, roles or access keys you gave us, and check your cloud's audit log for anything else created under them.

## 2. Your tools

During setup we may have been given admin rights to create Handbook's apps. In each tool you connected:

- [ ] **Slack:** remove us from the workspace or downgrade us from admin, and remove us as a collaborator on the Handbook app (app settings, **Collaborators**).
- [ ] **GitHub:** remove us from the organization, and from the Handbook GitHub App's managers if we were added.
- [ ] **Google:** remove us from the Google Cloud project that holds Handbook's sign-in client (**IAM**), and from any Workspace admin role.
- [ ] **Notion:** remove us from the workspace, or from the integration's owners.
- [ ] **Linear:** remove us from the workspace, or downgrade us from admin.

## 3. Handbook itself

- [ ] On Handbook's admin page, remove any account we used, so only your people remain.

## 4. Secrets we may have seen

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

## 5. Confirm

- [ ] Check the server's login history (`last`) and your cloud's audit log for any access from us after the date you removed it.
- [ ] Keep the image registry token we gave you: Handbook needs it to download upgrades. It only lets you download our images, not reach anything of yours.
- [ ] Tell us it is done, so we can close our record of the setup.
