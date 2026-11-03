# Tenant Direct Access: Overview Plan

Status: proposal. Nothing here is built. Applies to Handbook Cloud only; a
self-hosted customer already has the whole database.

Related: `docs/encryption-with-kms.md` (encryption proposal),
`docs/encrypted-search-plan.md` (search on encrypted data).

---

## 1. The goal

A company (say **Acme**) connects to our database with its own tools (a SQL
client, Metabase, Power BI, Python) and reads **its own data**:

- never another company's rows,
- never able to change anything,
- without slowing down the product.

```
Acme's SQL tool
     │  logs in as "acme_reader"
     ▼
Read replica (live read-only copy of our database)
     │  Postgres: "who is this?" → acme_reader → belongs to Acme
     │  Postgres silently adds:  WHERE org_id = Acme
     ▼
Only Acme's rows come back
```

The filter is **Row-Level Security (RLS)**: the database adds it, so the
tenant cannot remove it.

---

## 2. What we already have

- Every tenant table has `org_id`.
- `chunks` is partitioned per organization; partitions live in the
  `chunk_partitions` schema, which the app role cannot reach.
- `chunks` already has RLS for our app role (`handbook_app`, keyed on the
  `app.org_id` setting). Tenant readers get a **second, separate** rule.
- Only `chunks` has RLS today. Every other table is filtered by our code.

---

## 3. The read replica

A second database server that receives every change from the main one within
milliseconds to seconds. Nobody can write to it. Roles, RLS rules and mapping
rows created on the main database are copied to it automatically.

**One replica serves all tenants.** RLS keeps them apart.

| Hosting | How it is created |
| --- | --- |
| Supabase (today) | Dashboard → Infrastructure → Add read replica. Paid add-on; check plan requirements. |
| AWS RDS | Console → Actions → Create read replica, or `aws rds create-db-instance-read-replica`. |
| Own Postgres server | `pg_basebackup` from the main database, mark as standby, start. |

Our app keeps using the main database. Only tenants use the replica.

---

## 4. Without encryption

The data is stored as plain text. A tenant reads it directly over SQL.

### 4.1 One-time setup (once, before any customer)

| Step | What |
| --- | --- |
| A1 | Create the read replica. |
| A2 | Create the shared group `tenant_readers` (no login of its own). |
| A3 | Create two internal tables tenants cannot read: **login → company** mapping, and **allowed IPs** per company. |
| A4 | Add an RLS rule on `chunks` for `tenant_readers`: a row is visible only when its `org_id` is the company mapped to the logged-in user. |
| A5 | Grant `SELECT` on the parent `chunks` table only. No write grants, never the partitions. |
| A6 | Replica refuses every tenant login by default; SSL required. |
| A7 | Build the admin page: Handbook → **Admin → Data access**. |

Sketch of A3–A5:

```sql
CREATE ROLE tenant_readers NOLOGIN;

CREATE TABLE tenant_reader_roles (
    role_name name PRIMARY KEY,
    org_id    uuid NOT NULL REFERENCES organizations ON DELETE CASCADE);

CREATE FUNCTION reader_org() RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT org_id FROM tenant_reader_roles WHERE role_name = session_user $$;

CREATE POLICY chunks_tenant_reader ON chunks FOR SELECT TO tenant_readers
    USING (org_id = reader_org());

GRANT SELECT ON chunks TO tenant_readers;
```

### 4.2 Per-company flow

**Step 1: Admin requests access.**
Acme's admin opens **Admin → Data access → Request direct access**, enters the
IP addresses they will connect from, and ticks: *"This login can read all of
Acme's indexed data, including private documents."* Only admins see this page.

**Step 2: Backend creates the login (automatic).**

| Action | From the original brief |
| --- | --- |
| Create login `acme_reader`, random strong password | One login per tenant |
| Add it to `tenant_readers` | Shared group |
| Insert `acme_reader → Acme` into the mapping | RLS maps role to tenant |
| Read-only (inherited: `SELECT` only), `CONNECTION LIMIT 3`, `statement_timeout = 30s` | Limits per login |
| Save IPs and push them to the gatekeeper (§4.3) | Allowlisted IPs |

All of this runs on the main database and reaches the replica in seconds.

**Step 3: Admin receives connection details, once.**

```
postgresql://acme_reader:<password>@replica.ourapp.com:5432/ragdb?sslmode=verify-full
```

plus a **Download CA certificate** button. We do not keep a readable copy of
the password; a lost password is reset, not recovered.

**Step 4: Acme connects.** The replica checks IP is allowed, SSL with our
certificate, and the password. Any failure: refused.

**Step 5: Acme queries.**

```sql
SELECT * FROM chunks;
```

Postgres sees `acme_reader`, finds Acme in the mapping, applies the rule, and
returns only Acme's rows. Writes get "permission denied". A 4th concurrent
connection is refused.

**Step 6: Day to day.** Newly synced documents appear on the replica within
seconds. The admin can reset the password or change IPs at any time.

**Step 7: Revoke** (admin turns it off, we turn it off, or the company is
deleted):

1. Delete `acme_reader → Acme` from the mapping. The next query returns nothing.
2. End open sessions of `acme_reader`.
3. Drop the login.
4. Remove Acme's IPs from the list and the gatekeeper.

```
Admin requests (IPs + consent)
  → backend creates login, mapping, limits, IP rules
  → admin gets URI + certificate (once)
  → Acme connects (IP ✓ SSL ✓ password ✓)
  → queries → RLS returns only Acme's rows
  → revoke: remove mapping → end sessions → drop login → remove IPs
```

### 4.3 Where the allowed IPs live

**Recorded** in our own table (the source of truth, edited from the admin
page). **Enforced** where connections are accepted, which depends on hosting:

| Hosting | Enforced by | Granularity |
| --- | --- | --- |
| Own Postgres server | `pg_hba.conf` on the replica, reloaded on change | Per login (strongest) |
| AWS RDS | The replica's security group; the main database has its own that allows only our app | Per server |
| Supabase | Network Restrictions | Whole project (to verify); must also include our app's IPs |

`pg_hba.conf` example:

```
hostssl  ragdb  acme_reader      203.0.113.10/32   scram-sha-256
hostssl  ragdb  globex_reader    192.0.2.55/32     scram-sha-256
host     all    +tenant_readers  0.0.0.0/0         reject
```

### 4.4 Things that must be right

1. **The rule keys on the login, never on `app.org_id`.** Any user can
   `SET app.org_id` to another company. A reader is never a member of
   `handbook_app`.
2. **`session_user`, not `current_user`,** inside the `SECURITY DEFINER`
   lookup; there `current_user` is the function's owner.
3. **Read-only comes from the grants.** `default_transaction_read_only` can be
   switched off by the user; having only `SELECT` cannot.
4. **`statement_timeout` is only a default.** The user can `SET` it to 0. If it
   must hold, a small job ends `tenant_readers` queries past N seconds. Long
   replica queries can also hold back cleanup on the main database.
5. **Never grant the partitions.** Reading a partition directly skips the
   parent's rule.
6. **Catalog metadata.** Partition names are `org_<uuid>`, visible in
   `pg_class` with rough row counts. Minor, but do not promise "nothing about
   other companies is visible".
7. **Other tables.** `chunks` has no titles or links; tenants will want
   `documents`. Rather than adding RLS to each raw table, expose a `tenant`
   schema of a few views that already filter on `reader_org()` and grant only
   that schema. New internal tables are never exposed by accident, secrets
   stay out of reach, and our column renames do not break their queries.
8. **Private documents inside the company.** In Handbook each person sees only
   documents shared with them. A company login sees every row: private Drive
   files, private Slack channels, DMs. Either the admin consents to that
   (Step 1), or the views show only `doc_is_public` documents.

---

## 5. With encryption

With KMS encryption (`docs/encryption-with-kms.md`), the access flow above is
**unchanged**: login, mapping, RLS, replica, IP list, revoke. What changes is
what the tenant sees:

```
| id | document_id | content                          |
| 1  | 7a3…        | x7Kq!9pLm2@Zr…   (scrambled)     |
```

RLS still limits them to their own rows, but only our server can unscramble
the text. Three ways to give the tenant readable data:

### Option 1: The tenant decrypts it (keeps direct SQL)

Encryption layout (envelope encryption):

```
AWS KMS
  └── Acme's master key (never leaves KMS)
          │ locks
          ▼
      Acme's data key (stored LOCKED in our database)
          │ scrambles
          ▼
      Acme's rows
```

We give **Acme's own AWS account** permission to unlock **Acme's** data key
only. Acme then does what our server does, on their side.

Additions to the flow:

| Where | Addition |
| --- | --- |
| One-time | Publish the exact format (algorithm, nonce, any bound data) and a small decrypt script. |
| One-time | `tenant.data_key` view: each company's locked data key, visible only to that company. |
| Step 1 | Admin also enters their AWS account ID. |
| Step 2 | KMS grant: Acme's account may decrypt with Acme's key only. Skipped when Acme owns the key. |
| Step 7 | Remove the KMS grant too. |

Querying becomes:

```
1. Connect to the replica (IP, SSL, password)
2. SELECT the locked data key from tenant.data_key
3. Their script asks THEIR AWS KMS to unlock it   (logged in KMS)
4. SELECT * FROM chunks                           (scrambled, only Acme's)
5. Unscramble locally                             (readable text)
```

- ✅ Direct SQL kept; our staff still cannot read; every unlock is logged.
- ✅ With a customer-held key, the customer controls access completely.
- ❌ Needs an AWS account and our script; BI tools cannot read the text alone.
- ❌ No word filtering in SQL (`LIKE '%invoice%'` finds nothing).

### Option 2: Export API (no SQL for text)

Acme calls our API (`GET /api/export/documents`, `POST /api/export/search`)
with an API key. Our server decrypts and returns only Acme's data, and can
apply per-person document access. The SQL login can stay for unencrypted
fields (ids, dates, counts).

- ✅ Easiest for the customer; respects private documents.
- ❌ Not SQL; we decide what can be asked.

### Option 3: No encryption for companies that want SQL

A per-company setting; those companies use §4 unchanged.

- ✅ Simplest for them.
- ❌ Two storage paths to maintain forever; the companies wanting the most
  access lose the "staff cannot read it" protection.

### Readable even when encrypted

In every option, ids, dates, source provider and **embeddings** stay plain.
Embeddings can be partly turned back into text.

### Recommendation

| Customer wants | Give them |
| --- | --- |
| Readable data, easy | Option 2 |
| Direct SQL and encryption | Option 1 |
| Avoid if possible | Option 3 |

---

## 6. Decisions needed

1. **Tables:** `chunks` only, or `documents` (and which others) through a
   `tenant` view schema?
2. **Private documents:** whole company with admin consent, or public
   documents only?
3. **Hosting:** stay on Supabase (project-wide IP list, to verify) or move to
   AWS RDS / own server (replica-only, per-server or per-login IP rules)?
4. **With encryption:** Option 1, 2 or both?

## 7. Order of work

1. Read replica.
2. `tenant_readers`, mapping table, RLS rule, `tenant` view schema.
3. IP list and its push to the gatekeeper.
4. Admin page: request, credentials once, reset, IPs, revoke.
5. Query watchdog for long-running tenant queries.
6. Isolation test: a reader sees only its own company and cannot write.
7. With encryption: export API, then tenant-side decrypt (Option 1).
