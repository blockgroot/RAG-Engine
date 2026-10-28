# A Database for Each Company

| | |
| --- | --- |
| **Subject** | How Handbook Cloud could give every company its own database, created when the company's sign-up is approved, with the backend finding the right database from the company's `org_id`. |
| **Audience** | The team deciding whether to build this, and the engineers who would build it. |
| **Status** | Proposal. Nothing here is built. |
| **Related** | `docs/deployment-options.md` (the three offerings), `CLAUDE.md` §3 (isolation rules) |

---

## 1. Summary

Today every company's data sits in **one shared database**, kept apart by `org_id` on every row and every query. This proposal gives each company **its own database**:

- One **central database** keeps what is needed before we know the company: companies, users, sign-in, and a directory of "which company's data is where."
- Each company gets a **company database** with its documents, chats, connections, charts and everything else.
- Inside the backend, a **database router** turns the `org_id` of the request into a connection to that company's database. There is no separate REST service in between.
- The database is **created automatically when the company's sign-up is approved**.

It is feasible. It is also a large change to the most sensitive part of the code, with a running cost per company. Section 12 lists the cheaper alternatives, and section 13 the decision.

---

## 2. Why a router inside the backend, not a separate REST API

The first idea was a REST service between Handbook and the databases: Handbook asks it for data with the `org_id`, and it reads the right database.

That would mean rewriting every database call as an API call. Handbook talks to Postgres in **409 places across 75 files**, with SQL that depends on Postgres itself:

- vector search;
- the visibility predicate on every query;
- chart queries over stored rows;
- the job queue (`SKIP LOCKED`).

A REST layer would have to re-create all of that, and every request would gain an extra network hop.

The same goal is reached with a **router inside the backend**. Every database call already goes through one function, `get_connection()` in `app/db/connection.py`. Today it returns a connection from one shared pool. In this design it returns a connection to **the current company's** database. The SQL stays the same.

---

## 3. The architecture

```
                           ┌──────────────────────────────┐
                           │  Central database            │
 Browser ─┐                │  organizations, users,       │
 Slack ───┤   Handbook     │  sign-in tokens, sign-ups,   │
 Webhooks ┤   backend      │  company_databases (directory)│
          └─► ┌────────┐   │  connector_routes, job queue │
              │ router │──►└──────────────────────────────┘
              │        │──►┌───────────────┐
              │org_id →│   │ Company 1 DB  │  documents, chunks, chats,
              │  pool  │──►└───────────────┘  connections, charts, graph…
              └────────┘──►┌───────────────┐
                           │ Company 2 DB  │
                           └───────────────┘
```

The web app, the API code and the user experience are unchanged. Only where data is stored, and how the backend reaches it, changes.

---

## 4. What lives where

**Central database.** These are needed before the company is known, or are about routing:

| Table | Why central |
| --- | --- |
| `organizations` | The list of companies |
| `users`, `user_email_aliases` | Sign-in starts from an email; we must find the user's company first |
| `magic_link_tokens`, `email_change_requests`, `org_signup_requests` | Sign-in and sign-up happen before a company is chosen |
| `oauth_states`, `github_install_pending` | A tool's sign-in returns with only a `state` value; it must lead back to the company |
| `api_rate_counters` | Rate limits are per visitor, across companies |
| `ingestion_jobs` | One job queue the worker polls, each job naming its company (section 8) |
| **New:** `company_databases` | The directory: `org_id` → where its database is, its status, its schema version |
| **New:** `connector_routes` | Incoming events → company: Slack workspace ID, Notion workspace ID, Linear organization ID, Drive channel ID, GitHub installation ID |

**Company database.** Everything else, about 30 tables:

- `documents`, `chunks`, `conversations`, `conversation_turns`;
- `workspaces`, `workspace_members`;
- `oauth_connections` (the company's tool tokens);
- `drive_watch_channels`;
- `activity_facts`, `doc_tables`, `doc_table_rows`;
- `kg_*`, `actions`, `act_runs`, `mcp_servers`;
- `user_memory`, `feedback_and_gaps`, `gap_topics`, `query_answer_cache`;
- and the rest.

Each company database keeps its own `schema_marks`.

The `org_id` column **stays** on company tables, and every query keeps filtering on it. That is a second guard if the router ever picks the wrong database, and `CLAUDE.md` §3 requires it regardless.

---

## 5. How a request finds its database

`CLAUDE.md` already says `org_id` enters a request in **one place**: `app/api/deps.py`, from the signed session. The router builds on that:

1. `deps.py` reads the session, gets `org_id`, and sets it as the **current company** for this request. Technically this is a Python `contextvar`, which follows the request through the code.
2. `get_connection()` reads the current company, looks it up in `company_databases` (cached), and hands out a connection from that company's pool.
3. Code that needs central data asks for it by name: `get_connection(central=True)`.

Because the company is set once at the edge of the request, **most of the 409 call sites do not change**. They keep calling `get_connection()`.

**Requests that arrive without a session** find the company another way:

| Request | How the company is found |
| --- | --- |
| Sign-in with an email | `users` (central) gives the company |
| A tool's sign-in returning (`/auth/<tool>/callback`) | `oauth_states` (central) holds the company that started it |
| Slack event, Notion / Linear / Drive webhook | `connector_routes` (central): the tool's own ID → company |
| Sign-up approval | Central only, until the database is created |
| Background jobs | Each job in the central queue names its company (section 8) |

**The rule that must never be broken:** if no company is set, `get_connection()` **refuses**. It raises an error; it does not fall back to some database. A silent fallback is how one company's request would read another's data.

---

## 6. Creating a company's database at sign-up

When the platform owner approves a sign-up:

1. **Record it.** Insert the company into `organizations` and its entry into `company_databases` with status `creating`.
2. **Create the database.** Either `CREATE DATABASE handbook_org_<id>` on our database server, or create a new project through the hosting provider's API (Supabase, Neon); see section 7.
3. **Prepare it.** Create the `vector` and `pg_trgm` extensions and apply `schema.sql`.
4. **Register it.** Store its address in `company_databases`. The password goes in our secret store, never in the table as plain text. Set status `ready` and the schema version.
5. **Create the admin user** and send the sign-in link.

**If any step fails,** status becomes `failed`, the sign-up is not completed, and a retry picks up from the failed step. A company never gets a sign-in link to a half-made database.

**Timing:** steps 2–3 take a few seconds with `CREATE DATABASE`, but can take one to several minutes with a new hosted project. Then the approval says "Your workspace is being prepared; we will email you when it is ready."

---

## 7. Where the company databases live

| Option | How | Cost per company | Separation |
| --- | --- | --- | --- |
| **A. Many databases on one server** | `CREATE DATABASE` per company on one Postgres server (ours, or AWS RDS) | Almost none until the server is full | Separate data; shared server, so a heavy company can slow the rest |
| **B. One hosted project per company** | Supabase or Neon project per company, created by API | Roughly $10+/month per company on Supabase. Neon can be cheaper for quiet companies (it scales to zero), but the first request after idle is slower. | Separate data and separate server |
| **C. A mix** | Option A by default; option B for companies that pay for it | Per plan | Per plan |

**Recommended starting point: A**, with C later for large customers. Supabase's free plan allows only two projects, so B costs money from the third company onwards.

---

## 8. Background work

Today the worker polls one queue and runs one loop over all companies. With a database per company:

- **The sync queue stays central** (`ingestion_jobs`). Each job names its company. The worker sets the current company before running the job, exactly as `deps.py` does for a request, and clears it after.
- **Per-company loops:** schedulers, auto-sync, Drive renewal, expiry purges, backfills and gap grouping run **once per company**. The loop reads the list of `ready` companies from `company_databases`, then for each one sets the current company, does its work, and moves on. One company's unreachable database is logged and skipped. It never stops the loop for the others.
- **Threads:** a thread the code starts by hand does not inherit the current company. Every such place must set it explicitly, and a test must prove an unset company is refused (section 5).

---

## 9. Schema changes

`apply_schema()` runs at startup today, against one database. With many:

- On startup, the backend updates the **central** database first, then **every company database**, recording each one's version in `company_databases`.
- Schema changes stay **additive** (`CREATE … IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`), as now. Old and new versions of the code then both work while the update rolls through.
- A company whose update fails is marked `needs_migration` and retried, and is shown on our dashboard. The rest carry on.
- With many companies, startup gets slower. The updates move to a separate step run once per release, not on every start.

---

## 10. Connections

Each company database needs its own connection pool, in the API and in the worker.

- Pools are **opened when first needed** and **closed after a quiet period** (for example 10 minutes).
- Pools are **small**: 1 connection minimum, 3 maximum per company.
- A server allows a few hundred connections in total. 100 active companies × 3 connections × 2 processes = 600, which is too many. For option A, put **PgBouncer** (session mode, because of `register_vector`) in front of the server, or keep pools at 1–2.
- The router caches the directory, so finding a database does not cost a central query every time.

---

## 11. What this gives us, and what it costs

**Gains**

- One company's data cannot appear in another's query results, even if a query forgets its `org_id` filter. The query is simply in the wrong database, and the filter stays as a second guard.
- One company can be exported, restored or deleted as a unit (drop its database).
- A heavy company can be moved to a bigger server on its own (option B or C).
- A clear answer when buyers ask "is our data separate?"

**Costs and risks**

| Area | Cost or risk |
| --- | --- |
| Engineering | A multi-week change to how every request and job reaches data |
| The router | Choosing the wrong database is a data leak. It needs the "refuse when unset" rule, tests, and the `org_id` filters kept as a second guard. |
| Money | Option A: little. Option B: roughly $10+ per company per month. |
| Sign-up | Slower, and it can fail midway; needs status and retries |
| Operations | Backups, monitoring and schema updates multiply by the number of companies |
| Cross-company views | Our own usage numbers and billing must query every database, or keep central copies |
| Testing | Tests need a central database plus at least two company databases |

---

## 12. Cheaper alternatives

| Option | What it is | Effort | Separation |
| --- | --- | --- | --- |
| **Row-Level Security** | Same shared database; Postgres itself refuses to return another company's rows, even if our code forgets the filter | Days | Enforced by the database |
| **A schema per company** | One database; each company gets its own set of tables (a Postgres "schema"). The router switches schema per request (`SET search_path`) instead of switching database. | Medium: about 70% of this design (central data, routing, per-company loops), but **one connection pool** and no cost per company | Strong. This is how Onyx's Cloud separates companies. |
| **Self-Hosted** | The company runs its own copy of Handbook (installed by us if it prefers) | Already built (`deploy/selfhost/`, `docs/deployment-options.md`) | Complete |

---

## 13. Decision needed

1. **What problem are we solving?**
   - **Stronger protection against cross-company leaks:** Row-Level Security first, in days, with no cost per company.
   - **"Your data is in its own space," and one-step export or delete:** a schema per company.
   - **"Your data is in its own database"** for a specific customer: Self-Hosted, installed by us if it prefers.
   - **Every Cloud company in its own database:** this design.
2. If this design is chosen: **option A, B or C** (section 7).

## 14. If we build it: order of work

1. Split the tables into central and company (section 4), and add `company_databases` and `connector_routes`.
2. The router: current company set in `deps.py` and in the worker; `get_connection()` refuses when unset; pools per company. Tests prove two companies never see each other's data, and that an unset company is refused.
3. Creating a database at sign-up approval (section 6).
4. Per-company background loops and schema updates (sections 8–9).
5. Move existing companies: copy each one's rows from the shared database into its new database, check the counts, switch it over in the directory, and keep the shared copy until verified.
6. Our dashboard: each company's database status, schema version, and backup.
