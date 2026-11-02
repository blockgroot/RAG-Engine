# Encrypting Customer Data and Giving Tenants Direct Access

Status: proposal. Nothing here is built. This applies to Handbook Cloud only;
in Self-Hosted the customer runs everything themselves and we have no access.

---

## 1. The problem

Customers' documents are stored in our database. Today, anyone on our team with
database access could open it and read them.

We want two things:

1. **Our staff cannot read customer content.** Only our server can, only while
   answering a question, and every access is logged.
2. **A company can read its own data directly** with its own tools, and never
   another company's.

---

## 2. How we plan to solve it

### 2.1 Encryption with KMS keys

KMS is Amazon's key vault. It holds keys, never lets them out, only lets our
server use them, and logs every use. The **ARN** is just the ID of a key.

- **At sign-up:** we ask KMS to create a key for the company and save its ARN.
  A large customer can instead create the key in its own Amazon account and give
  us the ARN. It can then switch off our access at any time.
- **When a document is synced:** our server encrypts the text with the
  company's key before saving it. The database only ever stores encrypted text.
- **When someone asks a question:** the database returns the matching encrypted
  pieces, our server decrypts them in memory, and answers.

Anyone who opens the database sees only scrambled text.

### 2.2 Tenant direct access

Each company gets a **read-only database login** that can see only its own rows:

- One login per company (for example `acme_reader`).
- **Row-Level Security** in Postgres ties that login to the company, so even
  `SELECT *` returns only that company's rows.
- The login works only on a **read-only copy** of the database (a replica), not
  the main one, so their queries cannot slow down the product.
- Limits: read-only, a time limit per query, a cap on connections, SSL only,
  and only from IP addresses the company gives us.
- The company connects with a normal database address:
  `postgresql://acme_reader:<password>@replica.handbook...:5432/ragdb`
- We can remove access at any time by deleting the login.

---

## 3. Problems we may face

### 3.1 Keyword search stops working

Today the **database** does the searching: it looks for the words and sends back
only the matching pieces. With encryption, the database sees only scrambled
text and has no key, so it finds nothing.

**Example:** a company has 200,000 pieces. Someone asks "Was invoice 4521 paid?"

- Today: the database finds "4521" and returns 1 piece.
- Encrypted: the database finds nothing.
- Decrypt everything and search on our server: works, but every question moves
  all 200,000 pieces instead of 1. Far too slow.

**Fix:** when saving, also store a secret code for each word ("4521" becomes
`C9`). The database can match codes without knowing the words. Near matches
("invoices" vs "invoice") get a little weaker.

### 3.2 Charts stop working

Charts are added up inside the database ("total salary by team"). Encrypted
numbers cannot be added.

**Fix:** keep numbers unencrypted and encrypt only the text, or leave charts out
of the promise.

### 3.3 Customer text is in many places

Not just documents: chats, answers, titles, cached answers, knowledge-graph
facts, memory, uploads and logs all contain customer text. If one is missed,
the promise is false.

**Fix:** encrypt all of them, and add a test that fails if a new one is missed.

### 3.4 Our hosting weakens the promise

"Only our server can use the key" works when the server runs **on Amazon**.
We run on **Render**, where the Amazon password would sit in Render's settings,
and anyone with Render access could copy it.

**Fix:** move the backend to Amazon before making the promise.

### 3.5 Not fully "staff can never read it"

- Anyone who can change and deploy our code could make it reveal data.
- Search vectors stay unencrypted and can be partly turned back into text.
- The AI model sees the passages it answers from.

**Honest claim:** "Your content is encrypted with your own key, our staff have no
routine access, and every access is logged."

### 3.6 Other risks

- **KMS down:** if Amazon KMS is unavailable, we cannot read data. Keep keys in
  memory for a few minutes to soften this.
- **Key deleted:** the data is lost for good. Use Amazon's deletion waiting
  period and alert on it.
- **Existing data:** everything stored today must be encrypted once, company by
  company.
- **Support:** engineers cannot look at data to debug. We need an approved,
  logged emergency access path.

### 3.7 Problems with tenant direct access

- **It clashes with encryption.** Over a direct login, the company would see only
  scrambled text, because decryption happens in our server, not the database.
- **It ignores who may see what inside the company.** Handbook only shows each
  person documents shared with them. A company login sees every row, so private
  Drive files, private Slack channels and DMs would be visible to whoever holds
  the login.
- **It exposes our internal tables.** Things like saved tool tokens must be
  carefully hidden, and every new table is exposed unless someone remembers.
- **It ties our hands.** If we rename a column, their queries break.

**Suggested fix:** instead of a database login, give companies an **export API**
(for example "search my data" and "download all my data"). It decrypts on our
server and applies both rules: only their company, and only what each person may
see. If a company still wants a direct login, it must be limited to public,
unencrypted data.

---

## 4. Decisions needed

1. Is a customer asking for "your staff cannot read our data"? If not, do
   Row-Level Security now and keep encryption for later.
2. Do we move the backend to Amazon?
3. Is weaker keyword matching acceptable?
4. Direct database login or export API for tenant access?

## 5. Order of work

1. Row-Level Security (already chosen).
2. Move the backend to Amazon.
3. Per-company KMS keys and encrypt every place customer text is stored.
4. Keyword search with secret word codes.
5. Tenant access through an export API.
6. Encrypt existing data, then offer customer-held keys.
