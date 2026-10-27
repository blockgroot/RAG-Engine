# Self-Hosted Handbook: Requirements

| | |
| --- | --- |
| **Purpose** | Everything needed to ship Handbook as a package a company installs and runs on its own servers. It covers what Onyx gives its self-hosters, what we will give, what the customer must provide, and what has to change in our code first. |
| **Audience** | The team building the self-hosted version. |
| **Related** | `docs/deployment-options.md` (the three ways we offer Handbook). |
| **Sources** | Our code, read file by file. The Onyx repository (`deployment/`, `cli/`, `backend/ee/…/license`) and Onyx's documentation repository, both as of 26 October 2026. |

---

## 1. Scope of the first version

| In the first version | Later | Not planned |
| --- | --- | --- |
| Docker Compose on one Linux server | Kubernetes (Helm chart) | One install serving several companies |
| One company per install | Cloud setup scripts (Terraform) | Rollback by reversing database changes; restore a backup instead |
| AI models from outside providers: Claude, plus a hosted embedding service | Embedding and reranking models running inside the install | |
| HTTPS with an automatic certificate | Fully offline (air-gapped) installs | |
| An install script, an upgrade script and a backup script | A license key checked by the app | |
| Install, connector, upgrade and backup guides | Opt-in health reporting to us | |

The first version should let a technical person at a company go from an empty server to a working Handbook in under an hour. Most of that hour is creating the connector apps.

---

## 2. What Onyx gives its self-hosters, and what we will give

| Onyx provides | How Onyx does it | Handbook, first version |
| --- | --- | --- |
| One-line install | `install.sh` downloads a CLI, which asks for the version and mode, checks RAM and disk, can install Docker, and writes `.env` with generated secrets | **Yes:** `install.sh`, also asking for the domain and admin email |
| Lifecycle commands | `onyx-cli deploy status / logs / stop / upgrade / uninstall` | **Partly:** `upgrade.sh` and `backup.sh`; plain `docker compose` for the rest |
| A minimal mode | "Lite": Postgres only, 2 vCPU / 2 GB | Not needed: our full install is already Postgres plus three small services |
| Working without editing settings | Only one setting is required; everything else has a default | **Yes:** `install.sh` fills every required setting except the AI model key |
| HTTPS | nginx plus a Let's Encrypt script, or your own certificate | **Yes:** Caddy, which gets and renews certificates by itself |
| First admin | "The first person to sign up becomes the admin" | **Yes:** the admin is created at install (section 7, item 4) |
| AI model setup | Chosen in the admin screens; many providers, including local | Settings file; Claude is the supported model |
| Embedding model | Bundled local model; changeable in the admin screens (forces a full re-index) | A hosted embedding service; must produce 1024-number vectors |
| Connectors | Customer creates its own apps or tokens. Slack app templates are in the docs. | Customer creates its own apps. Slack and GitHub templates are generated with the customer's domain already filled in. |
| Sign-in for users | Email and password always; Google, OIDC and SAML in the admin screens (OIDC and SAML are paid) | Email sign-in links, as today |
| Upgrades | Database changes run at start; minor versions stay compatible; `upgrade` changes only the version | **Yes, the same:** our schema is applied at start |
| Rollback | Documented reverse database migration | Restore the backup taken before upgrading |
| Release rhythm | Weekly stable releases, nightly builds, a public "latest version" address | Tagged releases with notes. Rhythm to be decided. |
| Images | Docker Hub, base images pinned, a setting to use a private mirror | Our two images on GitHub's registry; base images pinned |
| Backups | Not provided: the customer's job | **Better:** `backup.sh` plus a restore guide |
| Outside managed services | Managed Postgres, Redis, OpenSearch; S3, GCS and Azure file storage | Managed Postgres; S3-compatible file storage |
| Sizing guide | Published minimum and preferred sizes | **Yes:** after we measure (section 4) |
| Offline installs | Save images to a file, load them offline, local models | Later |
| Health and status | Health checks on every service, a `status` command, metrics, dashboards | Health checks on every service, a `/health` page, and `docker compose ps` |
| Usage reporting | On by default, sent to Onyx; one setting turns it off | None in the first version; later opt-in only |
| License | Signed license file checked inside the app; seats, expiry, grace period; Community vs Enterprise features | **Open decision** (section 10) |

Three Onyx choices worth noting for later:

- **Slack bot in "Socket Mode."** The bot opens the connection to Slack, so the server needs no public address for Slack. Handbook uses Slack's web-address style (events sent to `/slack/events`), which requires one. Socket Mode would let companies run Handbook inside a private network.
- **Simple keys instead of apps where possible.** Onyx connects GitHub with a personal access token, Notion with an integration token, Linear with an API key, and Google Drive with a service account, so customers create fewer full apps. Handbook already accepts a Notion integration token (`NOTION_TOKEN`) and a Linear key (`LINEAR_TOKEN`). We should check they still give each person only the access they should have before offering them as the easy path.
- **The first sign-up becomes the admin.** This is the simplest possible single-company setup.

---

## 3. What we ship

```
Images (GitHub Container Registry, amd64 + arm64, built by .github/workflows/release.yml)
  ghcr.io/<owner>/handbook-api:<version>   backend; the same image runs the worker
  ghcr.io/<owner>/handbook-web:<version>   web app; sends /api to the backend

Install package (deploy/selfhost/, attached to each release as a .zip)
  docker-compose.yml      caddy, web, api, worker, tick, db
  Caddyfile               HTTPS for the customer's domain
  .env.example            every setting, grouped and explained
  install.sh              first install
  upgrade.sh              move to a new version
  backup.sh               database dump; reminder to keep the secret keys
  apps/slack-manifest.yml        Slack app template, domain filled in by install.sh
  apps/github-app-manifest.json  GitHub App template, domain filled in by install.sh
  docs/INSTALL.md  docs/CONNECTORS.md  docs/UPGRADE.md  docs/BACKUP.md
```

**Services in `docker-compose.yml`:**

| Service | Image | Job |
| --- | --- | --- |
| `caddy` | `caddy:2` (pinned) | The only public entry. Handles HTTPS and sends everything to `web`. |
| `web` | `handbook-web` | The pages people see. Forwards `/api/*` to `api`. |
| `api` | `handbook-api` | The backend, with `INGEST_WORKER_IN_API=false`. |
| `worker` | `handbook-api` | Syncs and schedulers (`python scripts/run_worker.py`). |
| `tick` | A tiny `curl` image | Calls `/internal/tick` every 10 minutes. Today GitHub Actions does this for Cloud. |
| `db` | `pgvector/pgvector:pg17` | Postgres with a data volume. Left out when the customer uses a managed database. |

---

## 4. What the customer must provide

**A server**
- Linux with Docker and Docker Compose.
- Size to be measured before publishing. Our starting estimate is 2 vCPU, 4 GB RAM, and 20 GB disk plus room for the indexed content. Cloud runs the backend in 512 MB today, so the API is small; the database grows with the amount of content.

**A web address and HTTPS**
- A domain or subdomain (for example `handbook.acme.com`) pointing at the server.
- Ports 80 and 443 open, so Caddy can get a certificate.
- HTTPS is required: the login cookie only works over HTTPS (`app/api/deps.py:48-53`).

**Accounts with outside services**

| Needed | For | Setting |
| --- | --- | --- |
| Claude API key | Answers, charts, Agent mode | `LLM_ADAPTER`, `LLM_MODEL`, `LLM_API_KEY` |
| Embedding service key (Jina today) | Search | `EMBEDDING_*`, model must give 1024-number vectors |
| Reranking service key (optional; same provider) | Better result ordering | `RERANKER_*`, or turn reranking off |
| Email: the company's SMTP server, or SendGrid | Sign-in links | `EMAIL_SENDER` and its settings |

**Outbound internet access** to: the Claude API, the embedding service, the email service, each connected tool's API (slack.com, googleapis.com, api.notion.com, api.linear.app, api.github.com), and ghcr.io for downloading images.

**Inbound internet access** from the connected tools, for sign-in returns and live updates (section 6).

**One app per connected tool,** created by the company (section 6).

---

## 5. Settings

**Generated by `install.sh`** (written to `.env`)

| Setting | Value |
| --- | --- |
| `AUTH_JWT_SECRET` | Random |
| `AUTH_ENCRYPTION_KEYS` | A new Fernet key |
| `POSTGRES_PASSWORD` | Random |

**Set by `docker-compose.yml`** (from `HANDBOOK_DOMAIN`)

| Setting | Value |
| --- | --- |
| `DATABASE_URL` | The bundled `db`, unless `.env` sets another |
| `FRONTEND_URL` | `https://<domain>`. Each tool's sign-in return address and Agent mode's (`*_REDIRECT_URI`, `MCP_REDIRECT_URI`) are worked out from it by the code. |
| `DRIVE_PUSH_BASE_URL` | `https://<domain>/api` |
| `HANDBOOK_MCP_URL` | `http://api:8000/mcp` (inside the install) |

**Entered by the customer**

| Setting | When |
| --- | --- |
| `LLM_ADAPTER`, `LLM_MODEL`, `LLM_API_KEY` | Always (the app checks the key at start and stops if it is wrong) |
| `EMBEDDING_API_KEY` (+ `RERANKER_API_KEY`) | Always |
| `EMAIL_SENDER` and SMTP or SendGrid values | Always: without them, sign-in links are only printed in the logs |
| Admin email | Always |
| Each connector's keys (section 6) | Per tool used |

**Set differently from Cloud's defaults, already in the shipped `.env.example`:**

| Setting | Self-hosted value | Why |
| --- | --- | --- |
| `EMBEDDING_BACKEND`, `RERANKER_BACKEND` | `remote` | The default `local` models are not in the image |
| `MODEL_WARMUP_ON_STARTUP` | `false` | Nothing local to warm up |
| `WEB_SEARCH_ENABLED` | `false` | On by default today; it sends questions to DuckDuckGo, so the company should choose |
| `INGEST_WORKER_IN_API` | `false` | The separate `worker` service does this work |
| `LLM_MAX_RPM`, `LLM_RESERVE_RPM` | Higher | Today's values suit a free model plan |
| `CLIENT_IP_HEADER` | `x-forwarded-for` | Today the first trusted header is Vercel's |

Every other setting keeps Cloud's value.

---

## 6. Connector apps and addresses

Each company registers its own app for each tool it uses (see `deployment-options.md`, section 5). These are the addresses entered in those apps. `install.sh` prints them with the real domain, and the API routes appear under `/api` through the web app.

| Tool | What the company creates | Addresses to enter | Keys copied into `.env` |
| --- | --- | --- | --- |
| Slack | A Slack app, from our template (`deploy/selfhost/apps/slack-manifest.yml`) | Sign-in `/api/auth/slack/callback` and `/api/act/mcp/callback` (Agent mode), events `/api/slack/events`, interactivity `/api/slack/interactivity`. Token rotation off. | `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_SIGNING_SECRET` |
| GitHub | A GitHub App, from our template (`deploy/selfhost/apps/github-app.html`) | Callbacks `/api/auth/github/callback` (must be first) and `/api/act/mcp/callback`; setup URL `/api/auth/github/callback`. Metadata, Contents, Pull requests read; Issues write (Agent mode). No webhooks. | `GITHUB_APP_SLUG`, `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY` |
| Google Drive | A Google OAuth client (set to "Internal" to skip Google's app review) | Sign-in `/api/auth/google/callback` and `/api/act/mcp/callback` (Agent mode). Live updates need the domain verified with Google; otherwise Drive is checked on a schedule. | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` |
| Notion | A Notion public integration | Sign-in `/api/auth/notion/callback`, webhook `/api/webhooks/notion` | `NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET`, `NOTION_WEBHOOK_VERIFICATION_TOKEN` |
| Linear | A Linear OAuth app | Sign-in `/api/auth/linear/callback`, webhook `/api/webhooks/linear` | `LINEAR_CLIENT_ID`, `LINEAR_CLIENT_SECRET`, `LINEAR_WEBHOOK_SECRET` |

Tools not used are left out; the app hides what is not configured. Only GitHub.com is supported, not GitHub Enterprise Server, because the GitHub addresses are fixed in the code.

---

## 7. Code changes needed before the first release

Found by reading the code. Each has the evidence and the planned fix.

**Must fix**

| # | Problem | Where | Fix |
| --- | --- | --- | --- |
| 1 | The worker script is not in the backend image, so a separate `worker` service cannot start | `Dockerfile:24` copies only two scripts | Also copy `scripts/run_worker.py` |
| 2 | Clean-up and some sync work run only when `/internal/tick` is called: Drive watch renewal, expiry of files and chats, backfills. Cloud gets this from GitHub Actions. | `app/jobs/worker.py:373-470`, `.github/workflows/tick.yml` | A `tick` service in Compose calling it every 10 minutes |
| 3 | Uploaded files can only be stored in Cloudinary; uploads fail without it | `app/attachments/blobstore.py:75-90` | An S3-compatible storage option (works with AWS S3, MinIO and others). A local-disk option for single-server installs is worth considering. |
| 4 | No single-company setup: anyone who reaches `/signup` can request a new company, and only the address in `OWNER_NOTIFICATION_EMAIL` can approve it, by email | `app/api/auth.py:71-125` | A single-company mode: the company and its admin are created at install, and public sign-up is turned off |
| 5 | Sign-up approval and email-change links are built from the address the backend sees, which inside Compose is the internal `api:8000`, so the links would not open | `app/api/auth.py:106`, `app/api/account.py:248` | Build them from `FRONTEND_URL`, like every other link |
| 6 | A shipped prompt names our own test channel, `#hand-book-testing` | `app/rag/prompts.py:1003` | Use a neutral example |

**Should fix soon**

| # | Problem | Where | Fix |
| --- | --- | --- | --- |
| 7 | The web image has the backend's address built in at build time | `frontend/Dockerfile` (`API_PROXY_TARGET`) | Fine while the service is always called `api`; write it down |
| 8 | Embedding and reranking services must be given an API key, even a local one that needs none | `app/embeddings/remote.py:44`, `app/reranker/remote.py:39-43` | Allow an empty key |
| 9 | The database is fixed at 1024-number vectors, so only embedding models of that size work | `app/db/schema.sql:156,1215` | State it in the guide; change only if a customer needs another model |
| 10 | Agent mode refuses MCP servers on private addresses, so a company's internal tools cannot be added | `app/actions/mcp_client.py:61-71` | An admin setting allowing named private addresses |
| 11 | Icons and fonts load from Google at run time | `frontend/app/globals.css:1`, `frontend/app/layout.tsx:2` | Bundle them in the image; needed for offline installs only |
| 12 | Six settings are read outside `app/config/settings.py`, against our rule that it is the only place | e.g. `CLIENT_IP_HEADER`, `CHAT_STREAM_WORD_DELAY_MS` | Move them into `settings.py` so `.env.example` lists every setting |

**Database requirements** (met by the shipped `db` service; a customer's managed Postgres must meet them too):
- the `vector` (pgvector 0.5 or later) and `pg_trgm` extensions;
- a database user allowed to create them, because the schema is applied at every start;
- a direct or session-mode connection, not a transaction-mode pooler (`app/db/connection.py:49-96`).

---

## 8. Install, upgrade and backup flow

**Install (customer)**
1. Prepare a Linux server with Docker, and point the domain at it.
2. Log in to our image registry with the access we give them.
3. Download the install package for a version and run `./install.sh`. It:
   1. checks Docker, Compose, RAM and disk;
   2. asks for the domain, admin email and Claude key;
   3. generates the secrets;
   4. writes `.env`;
   5. prints the connector addresses and app templates;
   6. starts everything;
   7. waits for `/health`;
   8. prints the login address.
4. Create the connector apps (`CONNECTORS.md`), paste their keys into `.env`, and run `docker compose up -d`.
5. The admin logs in, connects the tools and invites people.

**Upgrade.** `./upgrade.sh 1.2.0`:
1. takes a backup;
2. changes `HANDBOOK_VERSION`;
3. downloads the new images;
4. restarts.

The new backend applies its database changes on start, as Cloud does today. Releases within the same major version stay compatible. Customer edits to Compose go in `docker-compose.override.yml`, which upgrades never touch (Onyx's convention).

**Rollback.** Restore the backup from step 1 of the upgrade and set the old version. We do not ship reverse migrations.

**Backup.** `./backup.sh` dumps the database (`pg_dump`). The guide also tells the company to keep a separate copy of `.env`: without `AUTH_ENCRYPTION_KEYS`, every saved connector login is unreadable after a restore. Uploaded files are backed up with the storage the company chose.

---

## 9. How we support it

- **Releases:** each one has notes saying what changed and whether `.env` needs changes.
- **Supported versions:** the current release and the one before it.
- **Problem reports:** the company sends `docker compose ps`, the logs (`docker compose logs api worker`) and the version from `/health`. A single command that collects these, with secrets removed, is a later addition (Onyx's `status` command is the model).
- **Security fixes:** announced to a named contact at each company.

---

## 10. Decisions needed before the first release

| Decision | Options | Note |
| --- | --- | --- |
| Is the code public? | Keep the repo public, or make it private | **The repo is public today** (`blockgroot/RAG-Engine`), so the self-hosted code is already readable by anyone. A private registry protects the images only. |
| License | Commercial license and contract only; a license key checked by the app; or open core (Onyx: MIT plus paid `ee/` features) | Onyx's key is a signed file checked inside the app, with seats, expiry and a grace period |
| Which features are paid | e.g. SSO, Agent mode, analytics | Onyx keeps SSO, permission sync, user groups, analytics and white-labelling for paid plans |
| File storage | S3-compatible only, or also local disk | Local disk is simplest for one server; S3 suits cloud installs |
| Release rhythm | Weekly, as Onyx; or monthly | Self-hosted customers upgrade less often than we release |
| Slack connection type | Events to a public address (today), or also Socket Mode | Socket Mode lets Handbook run with no public address for Slack |
| Easy-path keys | Offer Notion and Linear keys instead of full apps | Must first check access stays per person |

---

## 11. Build order

1. **Done:** web image and release workflow (`frontend/Dockerfile`, `.github/workflows/release.yml`).
2. Code fixes 1, 2, 5 and 6, plus the Compose file, Caddyfile and `.env.example`. Test on a fresh server.
3. Fix 4 (single-company mode) and fix 3 (file storage).
4. `install.sh`, `upgrade.sh`, `backup.sh`, the Slack and GitHub templates, and the four guides.
5. Measure memory, CPU and disk on a test install with real content, then publish the sizing guide.
6. First release tag and a pilot with one friendly company.
