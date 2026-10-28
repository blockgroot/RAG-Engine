# Installing Handbook

This guide takes you from an empty server to signing in to Handbook. After that, [CONNECTORS.md](CONNECTORS.md) connects your tools.

## Before you start

**A server**
- Linux with Docker Engine and the Docker Compose plugin (version 2.20 or later). Check with `docker compose version`.
- About 2 CPUs, 4 GB of memory and 20 GB of disk, plus room for your indexed content. These are starting figures; a sizing guide will follow.

**A web address**
- A domain or subdomain for Handbook, for example `handbook.yourcompany.com`, with a DNS record pointing at the server.
- Ports 80 and 443 open to the internet. Handbook gets its HTTPS certificate automatically, and Slack, Notion and Linear must be able to reach it.

**Accounts**

| What | Why |
| --- | --- |
| A Claude API key ([console.anthropic.com](https://console.anthropic.com)) | Answers, charts and Agent mode |
| A Jina API key ([jina.ai](https://jina.ai)) | Search. Another embedding service can be used instead; see "Choosing an embedding model" below. |
| An SMTP server for sending email | Sign-in links are sent by email |
| A username and token for our image registry | We send them with your license. The installer asks for them. |

## 1. Get the install package

Download `handbook-selfhost-<version>.zip` from the latest release at [github.com/blockgroot/handbook-releases/releases](https://github.com/blockgroot/handbook-releases/releases), and unzip it on the server. For example, for version 1.0.0:

```sh
curl -LO https://github.com/blockgroot/handbook-releases/releases/download/v1.0.0/handbook-selfhost-1.0.0.zip
unzip handbook-selfhost-1.0.0.zip
```

This creates a `handbook` folder. Every command in these guides is run from inside it.

```
handbook/
├── install.sh   upgrade.sh   backup.sh
├── docker-compose.yml   Caddyfile   .env.example   VERSION
├── apps/        templates for your Slack and GitHub apps
└── docs/        these guides
```

## 2. Run the installer

```sh
cd handbook
./install.sh
```

It asks for your domain, your company's name, your work email, the Claude and Jina keys, your SMTP details, and the registry username and token we sent you. Everything else is generated or has a default. Then it:

1. generates the four secrets;
2. writes `.env`, readable only by you;
3. writes your domain into the Slack and GitHub templates;
4. logs in to our image registry, downloads the images and starts Handbook;
5. waits until Handbook is healthy;
6. creates your company, with you as its admin, and prints your next steps.

The first start takes a few minutes. You can run `./install.sh` again at any time: it keeps your `.env` and simply (re)starts Handbook.

> **Keep a copy of `.env` somewhere safe.** `AUTH_ENCRYPTION_KEYS` protects every connected tool's sign-in. If it is lost, every tool has to be connected again.

<details>
<summary>Installing by hand instead</summary>

1. `cp .env.example .env` and fill in every value; the file explains each one.
2. Generate the four secrets once:
   ```sh
   openssl rand -hex 32                              # POSTGRES_PASSWORD, AUTH_JWT_SECRET, UPDATER_TOKEN (once each)
   openssl rand -base64 32 | tr '+/' '-_'            # AUTH_ENCRYPTION_KEYS
   ```
3. Replace `HANDBOOK_DOMAIN` in `apps/slack-manifest.yml` and `apps/github-app.html` with your domain.
4. Log in to our registry with the username and token we sent you (`docker login ghcr.io`), then run `docker compose up -d`.
5. Once `api` is healthy, create your company:
   ```sh
   docker compose exec api python scripts/create_company.py "Your Company" you@yourcompany.com
   ```

</details>

## 3. If Handbook does not start

Check the services:

```sh
docker compose ps
```

All six services (`caddy`, `web`, `api`, `worker`, `updater`, `db`) should show `running`, and `api` should show `healthy`.

If `api` keeps restarting, its log names the problem:

```sh
docker compose logs api
```

Handbook checks its settings when it starts and stops with a message when something is wrong. Two examples:
- `LLM check: FAILED ...` means the Claude key or model name is wrong.
- `Embedding check: FAILED ...` means the embedding model does not fit (see below).

## 4. Sign in

1. Open `https://<your domain>` and enter the work email you gave the installer.
2. Open the sign-in link that arrives by email. You are in as your company's admin.

There is no public sign-up page: your Handbook holds only your company. Colleagues join when an admin invites them from the admin page.

## 5. Get Handbook ready for your company

Follow [CHECKLIST.md](CHECKLIST.md): a checklist for connecting your tools ([CONNECTORS.md](CONNECTORS.md)), checking the answers, deciding who sees what, and inviting your colleagues. If we installed Handbook for you, its last section removes our access.

## Everyday commands

Run these from the Handbook folder.

| To | Run |
| --- | --- |
| See if everything is running | `docker compose ps` |
| Read the logs | `docker compose logs --tail 100 api worker` (add `-f` to follow) |
| Apply a change you made in `.env` | `docker compose up -d` |
| Stop Handbook (data is kept) | `docker compose stop` |
| Start it again | `docker compose up -d` |
| Back up | `./backup.sh` ([BACKUP.md](BACKUP.md)) |
| Upgrade | `./upgrade.sh <version>` ([UPGRADE.md](UPGRADE.md)) |
| Remove Handbook but keep the data | `docker compose down` |
| Remove Handbook **and delete all its data** | `docker compose down -v` (cannot be undone; back up first) |

Handbook starts again by itself after a server restart.

## Choosing an embedding model

Handbook turns your documents into vectors (lists of numbers) to search them. It uses Jina's `jina-embeddings-v3` unless you choose another model.

You can use **any** OpenAI-compatible embedding service, including a model running on your own server, by setting `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL` and `EMBEDDING_API_KEY` in `.env`. One rule applies: **Handbook's database stores vectors of 1024 numbers.**

| Your model | What to do |
| --- | --- |
| Returns 1024 numbers (Jina v3, BGE-M3, ...) | Nothing more |
| Can shorten its vectors on request (OpenAI `text-embedding-3`, Cohere v4, ...) | Also set `EMBEDDING_SEND_DIMENSIONS=true` |
| Returns another size | See "Changing the embedding size" |

Handbook checks this every time it starts. If the sizes differ, it refuses to start and names both sizes, rather than failing later in the middle of a sync.

**Choose before your first sync.** Vectors from two different models cannot be compared, so changing the model later means syncing every document again.

### Changing the embedding size

Changing the database's vector size is not a self-service step yet. Contact us **before your first sync** and we will give you the steps for your version.

## Current limitations

- **Chat file uploads** are kept in Handbook's own database, so they stay on your server and are included in backups. Only the text read from each file is kept, not the original file. To store files with Cloudinary instead, fill in the `CLOUDINARY_*` settings.
- **Only GitHub.com** is supported, not GitHub Enterprise Server.
- **Slack, Notion and Linear send live updates over the internet,** so Handbook must be reachable at its public address. Without that, your tools are still synced on a schedule, but the Slack bot cannot answer questions.
