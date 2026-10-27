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
| Access to our image registry | We send you a username and token with your license |

## 1. Get the install package

Download the install package for your version and unzip it on the server:

```
handbook/
├── docker-compose.yml
├── Caddyfile
├── .env.example
├── apps/        templates for your Slack and GitHub apps
└── docs/        these guides
```

Log in to our image registry with the username and token we gave you:

```sh
docker login ghcr.io
```

## 2. Fill in the settings

```sh
cd handbook
cp .env.example .env
```

Open `.env` and fill in every value. The file explains each one. In short:

1. `HANDBOOK_VERSION`: the version you downloaded. `HANDBOOK_DOMAIN`: your domain, without `https://`.
2. Three secrets. Generate each one once:
   ```sh
   openssl rand -hex 32    # run twice: POSTGRES_PASSWORD, AUTH_JWT_SECRET
   python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
                           # AUTH_ENCRYPTION_KEYS
   ```
3. `LLM_API_KEY` (Claude) and `EMBEDDING_API_KEY` (Jina).
4. Your SMTP server details.
5. `OWNER_NOTIFICATION_EMAIL`: your own address. You approve your company's sign-up with it (step 4).

Leave the connector keys empty for now.

> **Keep a copy of `.env` somewhere safe.** `AUTH_ENCRYPTION_KEYS` protects every connected tool's sign-in. If it is lost, every tool has to be connected again.

## 3. Start Handbook

```sh
docker compose up -d
```

The first start downloads the images and takes a few minutes. Check that everything is running:

```sh
docker compose ps
```

All five services (`caddy`, `web`, `api`, `worker`, `db`) should show `running`, and `api` should show `healthy`.

If `api` keeps restarting, its log names the problem:

```sh
docker compose logs api
```

Handbook checks its settings when it starts and stops with a message when something is wrong. Two examples:
- `LLM check: FAILED ...` means the Claude key or model name is wrong.
- `Embedding check: FAILED ...` means the embedding model does not fit (see below).

## 4. Create your company and sign in

1. Open `https://<your domain>/signup`, enter your work email and company name.
2. An approval email arrives at `OWNER_NOTIFICATION_EMAIL`. Open it and press **Approve**.
3. A sign-in link arrives at the email you signed up with. Open it, and you are in as your company's admin.

> This approval step exists because the same software also runs our multi-company cloud. A setting that creates your company during installation and turns public sign-up off is planned.

## 5. Connect your tools

Follow [CONNECTORS.md](CONNECTORS.md) for each tool you use. Then, in Handbook, choose what each tool syncs and invite your colleagues.

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

- **Chat file uploads** are stored with Cloudinary. Fill in the `CLOUDINARY_*` settings, or leave them empty to turn uploads off. Support for your own storage (Amazon S3 and compatible services) is planned.
- **Only GitHub.com** is supported, not GitHub Enterprise Server.
- **Slack, Notion and Linear send live updates over the internet,** so Handbook must be reachable at its public address. Without that, your tools are still synced on a schedule, but the Slack bot cannot answer questions.
