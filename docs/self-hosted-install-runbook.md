# Installing Handbook for a Company

Our checklist for Self-Hosted, "we install it for you" (`docs/deployment-options.md`, section 2). The company's own guides are in `deploy/selfhost/docs/`; this page covers only what is different when we do the work.

## 1. Before the call

Ask the company for:

| What | Who usually provides it |
| --- | --- |
| A Linux server (2 CPUs, 4 GB memory, 20 GB disk) with Docker, and SSH access for us | Their IT or cloud team |
| A domain for Handbook, with a DNS record pointing at the server, and ports 80 and 443 open | Their IT |
| An API key for their AI model (Claude, OpenAI, Gemini or another) and for search (Jina or another), on their accounts | Their IT or finance |
| SMTP details for sending email | Their IT |
| The company name and the first admin's work email | Our contact |
| Which tools to connect, and an admin for each (Slack workspace admin, GitHub organization owner, Google Workspace admin, Notion workspace owner, Linear admin) | Our contact |

Agree in writing what we may access and until when. We do not take copies of their data or `.env`.

## 2. Install

1. Send them the pull token for our image registry (one per company, read-only), unless the images are public.
2. On their server, download and unzip `handbook-selfhost-<version>.zip` from https://github.com/blockgroot/handbook-releases/releases and run `./install.sh` once (`INSTALL.md`). It creates `.env` and stops.
3. **Their IT person fills in `.env`, not us.** It holds their API keys, passwords and the secrets that protect their data. Walk them through the sections on screen share if they want, but never type or copy its values, and never take a copy of the file. Then they run `./install.sh` again.
4. Check: `docker compose ps` shows all six services running and `api` healthy, and `https://<domain>` opens with a valid certificate.
5. The admin signs in at `https://<domain>` with the email set as `HANDBOOK_ADMIN_EMAIL`.

## 3. Connect the tools

Share the screen with each tool's admin and follow `CONNECTORS.md`. The apps are created in the **company's** accounts, never ours, and their keys go only into the company's `.env`. After each one, run `docker compose up -d`, connect the tool in Handbook, pick what it syncs, and check that a first sync finishes.

## 4. Before handing over

- Ask a question that each connected tool should answer, and check the answer cites it.
- Run `./backup.sh` once, and set up the daily backup and the off-server copy (`BACKUP.md`) with their IT.
- Show their IT `INSTALL.md` ("Everyday commands") and `UPGRADE.md`.
- Go through `CHECKLIST.md` with the admin: sections 1–3 together, and the rest (spaces, settings, invites) is theirs to finish.
- Add the admin's email to our release email list.

## 5. Hand back access

Remove our SSH key and any accounts we were given, and tell the company it is done. Point them to section 8 of `CHECKLIST.md` (in the package's `docs/`) so they can check every item themselves; it also lists which secrets to change if their policy asks for it. If they keep us on a support plan for upgrades, keep only the access the plan names, and write down what it is.
