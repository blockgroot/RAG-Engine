# Keeping Each Company's Data Separate: The Options, Explained Simply

This guide explains each idea the team listed, in plain words. It says what each
option is, what it gives us, what it doesn't, and why, for Handbook specifically.

---

## First, one picture to keep in mind

Think of our database as a **building where companies keep their files**.

- Today, every company's files are in **one big shared room**.
- Each file has a **sticker with the company's name** on it.
- Whenever someone asks for files, our app **reads the sticker** and only hands
  back files with the right company name.

All the options below are different ways of keeping companies from seeing each
other's files. Some add a stronger lock, some give each company its own room,
and some give them their own building.

There is also a **second rule** inside each company: **not every employee can see
every file.** If a Google Doc was shared with only three people, only those three
can get answers from it. Keep this in mind, because it explains why some options
won't work for us.

---

## Option 1: One shared room, and our app checks the sticker

**This is what we do today.**

**In simple words:** All companies' data sits together. Every time our app looks
something up, it says "only give me Company A's things."

**What we get**
- It's the cheapest and simplest setup.
- Search is fast, and it respects who is allowed to see what.
- It's easy for us to see totals across all companies (usage, billing).

**What we don't get**
- **If a developer forgets the check even once, Company A could see Company B's
  data.** Nothing else would stop it.
- We can't honestly tell a customer "your data is kept separately." It's together,
  just labelled.
- Deleting or exporting one company's data means hunting through every table.

**Why:** The only protection is our own code remembering to check. People make
mistakes, and our tests only catch the mistakes they were written to look for.

### 1b. Giving each company an API key

**In simple words:** We give each company a password-like key so their own tools
can pull their data from Handbook.

**What we get:** Customers can connect Handbook to their own reports or scripts.

**What we don't get**
- A key belongs to **the whole company, not a person**. Handbook decides what
  you can see based on **who you are** (your email). A company key has no "who."
  It would either see only public documents (not very useful), or see
  **everything, including private files only certain people should see**
  (dangerous).
- It doesn't make data any safer from other companies. It's a new door, not a
  new lock.

---

## Option 2: Same shared room, but the database itself checks the sticker

This is called **Row-Level Security**.

**In simple words:** Today our *app* checks the sticker. With this option, the
*database* checks it too. Even if a developer forgets, the database says "you're
Company A, so you only get Company A's things."

It's like a security guard at the door, not just a polite staff member.

**What we get**
- **A forgotten check no longer leaks data.** The database quietly returns
  nothing from other companies.
- Takes **days to about two weeks** to build.
- **No extra cost** per company. Nothing else about the product changes.

**What we don't get**
- If our app tells the guard the **wrong company name**, the guard believes it.
  This protects against *forgetting*, not against *giving the wrong name*.
- Data still lives in the same room. We still can't say "your data is stored
  separately."
- Deleting or exporting one company is still not a one-step job.

**Why there's a catch:** The database lets its "owner" account skip the guard.
If our app logs in as that owner (likely on our current hosting), the guard does
nothing. So we must make the app log in as a normal, limited account.

### 2b. Letting each company run its own database queries

**In simple words:** Give each company direct read access to the database so
they can run their own queries.

**What we get:** Customers' data teams could build their own reports.

**Why we should NOT do this:** The guard only knows **which company** someone is
from, not **which person**. So any employee with this access could read **every
private document, private Slack channel and DM** their company has connected,
including ones they were never allowed to see. That breaks our most important
promise. It would also expose sensitive internal things like saved login tokens.

---

## Option 3: A separate box per company in a "vector database"

**In simple words:** Some AI products keep their search data (called "vectors")
in a separate specialised database, and give each company its own box in it.

**Why this doesn't apply to us:** We don't have a separate vector database. Our
search data lives **in the same database** as everything else. That's on purpose:
it lets one lookup do three things at once:
1. only this company's data,
2. only documents this person is allowed to see,
3. the most relevant results.

If we split the search data out, the "who is allowed to see this" rule would have
to be copied into a second system and kept in sync. A mismatch means a leak.

**What we'd get:** Nothing we don't already have, plus one more system to run.

### 3b. Our own small, safe API (search, export)

**In simple words:** Instead of giving customers database access, we offer a few
safe web links, for example "search my data" and "download all my data."

**What we get**
- **"Download all my data"** is genuinely useful for contracts and deletion
  requests.
- "Search my data" lets customers use Handbook from their own tools.

**What we don't get**
- Giving out raw search data ("vectors") is pointless for customers and a bit
  risky: it can be partly turned back into the original text.
- Like option 1b, it needs to know **which person** is asking, not just which
  company, or private documents get exposed.

---

## Option 4: Each company gets its own room in the same building

This is called **a schema per company**.

**In simple words:** Still one database, but each company gets its **own set of
tables**, like its own room. When Company A asks for something, the app only
opens Company A's room.

**What we get**
- A forgotten check **can't** reach another company, because their room isn't
  even open.
- **Exporting or deleting one company is one step:** copy or remove its room.
- We can fairly say "your data is kept in its own space."
- **No extra cost** per company, since it's still one database.

**What we don't get**
- All rooms are still in one building. If one company is very busy, others can
  feel slower.
- Every change to our tables must be done **once per company**. With hundreds of
  companies, updates get slow. Past about a thousand, the building struggles.
- Our own totals (usage, billing) must visit every room.

**Effort:** Several weeks. It's most of the work of option 5, without the extra
running cost.

---

## Option 5: Each company gets its own database

**This is the proposal in `docs/database-per-company.md`.**

**In simple words:** Each company gets **its own separate database**, like its own
filing cabinet, often all in the same server room.

**What we get**
- Very strong separation. A mistake can't reach another company's data.
- One company can be moved, restored or deleted by itself.
- We can honestly say "**your data is in its own database**."

**What we don't get, or what it costs**
- Each company needs its **own open line** to its database. With many companies,
  we run out of lines unless we add extra tooling.
- Sign-up gets slower: a new database must be created and prepared first, and
  that can fail partway.
- Backups, monitoring and updates have to be done **for every company**.
- It's a **multi-week change** to the most sensitive part of the product. Sending
  a request to the wrong database would itself be a leak.

---

## Option 6: A whole separate setup for one customer

**In simple words:** One customer gets their **own servers**: database, storage,
everything. Nothing is shared with anyone.

**What we get**
- Full separation, and no slowdowns caused by other customers.
- Meets strict requirements for banks, hospitals and government.
- **We can already do this.** We have a Self-Hosted version of Handbook. We can
  install it on servers we run just for that customer. No new code needed.

**What we don't get**
- It's expensive: each customer has their own servers, updates and monitoring.
- Only makes sense for **large or regulated customers** who pay for it.

---

## Option 7: The data stays in the customer's own cloud

This is called **BYOC ("Bring Your Own Cloud")**.

**In simple words:** All the data and processing run in **the customer's own
cloud account**. We only manage the product from outside (updates, settings),
without seeing their data.

**What we already have:** Our **Self-Hosted** version already runs *entirely* in
the customer's cloud. That's an even stronger promise: "**nothing runs on our
side at all**."

**What we don't have yet**
- A way to **manage their copy remotely** (push updates, check health) without
  seeing their data. Building that would take **months**.
- Easy support. When something breaks, we can't look inside without their
  permission. That's the point, but it makes fixing problems slower.

**Why wait:** Only a customer who says "you manage it, but you can't see it"
needs this. Until then, Self-Hosted covers "our data never leaves our cloud."

---

## All options at a glance

| Option | Stops "someone forgot the check" leaks | Can we say "your data is separate"? | Easy to export or delete one company | Extra cost per company | Work needed |
|---|---|---|---|---|---|
| 1. Shared room, app checks (today) | No | No | No | None | Already done |
| 2. Shared room, database also checks | **Yes** | No | No | None | Days to 2 weeks |
| 3. Separate vector boxes | Doesn't apply to us | | | | |
| 4. Own room per company | **Yes** | Partly ("own space") | **Yes** | None | Several weeks |
| 5. Own database per company | **Yes** | **Yes** | **Yes** | Low to medium | Many weeks |
| 6. Own servers per customer | **Yes** | **Yes** | **Yes** | High | Possible today (Self-Hosted) |
| 7. Runs in customer's cloud | **Yes** | **Yes, strongest** | **Yes** | Customer pays | Self-Hosted today; full version is months |

**About the extras (API keys, our own API, direct database access):** these are
ways for customers to **get their data out**, not ways to **keep companies
apart**. All three hit the same problem: Handbook decides access **per person**,
and these work **per company**. So they risk showing private documents to the
wrong employee.

---

## What we recommend

1. **Now:** Option 2 (the database also checks the sticker). It's cheap and quick,
   and it removes the biggest risk.
2. **When a customer asks "is our data separate?":** offer Self-Hosted, or a
   private setup we run for them (options 6 and 7). Both already work.
3. **Only if our cloud product itself must promise separate storage for every
   company:** do option 4 (own room) before option 5 (own database). It's most of
   the benefit at much lower cost.
4. **Don't do:** option 3 (doesn't fit how we're built) and option 2b (it would
   expose private documents to the wrong people).
