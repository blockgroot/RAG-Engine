# Searching Encrypted Data: The Plan

Status: proposal. Nothing here is built. It applies to Handbook Cloud if we
encrypt customer content (see `docs/encryption-with-kms.md`).

---

## 1. The problem

If a company's documents are stored encrypted, the database only sees
scrambled text. Today two things rely on the database reading that text:

1. **Keyword search**: finding the exact words of a question ("invoice 4521").
2. **Charts**: adding up numbers ("total salary by team").

Search by meaning keeps working, because it uses vectors, and vectors are
not encrypted. This plan covers how keyword search and charts keep working
too.

---

## 2. Why vectors alone are not enough

A vector describes **what a text is about**, not **exactly what it says**.

| Text | What the vector "sees" |
| --- | --- |
| "Invoice 4521 was paid on 3 March" | invoice + payment |
| "Invoice 4512 is overdue" | invoice + payment |

The two look almost the same to a vector, so a search for "invoice 4521"
cannot reliably tell them apart. The same happens with names (Priya vs
Priyanka), codes (`HB-204` vs `HB-240`) and email addresses.

A vector also cannot add numbers: "Salary ₹50,000" and "Salary ₹5,00,000"
look nearly the same.

So we keep vectors for meaning, and add **secret word codes** for exact
words.

---

## 3. Keyword search with secret word codes

### The idea

Each word is turned into a **secret code** using the company's own key.

- The same word with the same key always gives the same code.
- Without the key, nobody can tell which word a code stands for.
- Each company has a different key, so the same word has a different code
  in each company.

The database stores and searches the codes. It never sees the words.

### What is stored for each piece of a document

| Column | Example |
| --- | --- |
| Text | encrypted: `x7Kq!9pLm…` |
| Meaning vector | `[0.12, 0.88, …]` (as today) |
| Word codes | `{A1, C9, D7, F3, G2, K2}` (new) |

### Example: saving a document

A Notion page arrives: *"Invoices 4521 and 4522 were paid on 3 March."*

At this moment our server holds the readable text, before saving it.

1. **Split into words:** Invoices, 4521, and, 4522, were, paid, on, 3, March
2. **Clean them up**, with the same rules every time:
   - lowercase,
   - simplify word endings ("invoices" → "invoic"),
   - drop very common words ("and", "were", "on").

   Result: `invoic, 4521, 4522, paid, 3, march`
3. **Turn each word into a code** with the company's key:

   | word | code |
   | --- | --- |
   | invoic | A1 |
   | 4521 | C9 |
   | 4522 | D7 |
   | paid | F3 |
   | 3 | G2 |
   | march | K2 |

4. **Encrypt the text** and **make the meaning vector** (as today).
5. **Save:** encrypted text + meaning vector + codes.

Nothing is hardcoded. The words come from the document itself; the codes come
from a formula (a keyed hash, HMAC) plus the company's key.

### Example: asking a question

Someone asks: *"Was invoice 4521 paid?"*

1. The server knows the company from the login and takes its key (kept in
   memory for a few minutes).
2. It prepares two things from the question:
   - a **meaning vector**, as today;
   - **word codes**: `invoic → A1`, `4521 → C9`, `paid → F3`.
3. It runs **two searches at the same time**:

   | Search | What it asks the database | Finds |
   | --- | --- | --- |
   | By meaning | "Pieces with the closest vectors" | pieces about invoices and payments |
   | By codes | "Pieces that have code A1, C9 or F3" | pieces with those exact words |

   The code search uses an index, like the index at the back of a book:

   | code | pieces that have it |
   | --- | --- |
   | A1 | #1, #2 |
   | C9 | **#1** |
   | F3 | #1, #3 |

4. The server **decrypts only the pieces that came back** (about 200 at most).
5. Everything after this works **exactly as today**: keyword ranking (BM25) on
   the real text, merging the two lists, reranking, the relevance check, and
   the AI writing the answer.

```
Question
   │
   ├─► meaning vector ──► nearest vectors ───────────────┐
   │                                                     ├─► merge ─► rerank ─► AI answer
   └─► word codes ──► pieces with those codes            │
                       ─► decrypt ─► keyword ranking ────┘
```

### How accurate it is

Close to today. The final keyword ranking already runs on our server, on
the real text, and that does not change. Only the first step (finding the
candidates) uses codes instead of words.

| Area | Difference from today |
| --- | --- |
| Exact words, numbers, names, codes | Same |
| Plurals ("invoices" vs "invoice") | Same, if our server simplifies words the way Postgres does (both use the standard "Snowball" method) |
| Typos ("invoce") | Not matched, the same as today |
| Spelling correction of questions | Needs a per-company word list kept encrypted, since it reads every piece's text today |
| Fuzzy matching of names in the knowledge graph | Becomes exact-match only |

We should confirm this by running the same questions through today's search
and the new one, and comparing results, before relying on it.

### How fast it is

| Step | Extra time |
| --- | --- |
| Making codes for the question | under 1 ms |
| Code search in the database | about the same as today's word search |
| Decrypting about 200 pieces | 1–5 ms |
| Fetching the key from KMS (only every few minutes) | 50–150 ms from Render, 10–20 ms on Amazon |

An answer already takes 3–10 seconds, mostly the AI writing it, so the extra
time is not noticeable.

### What it costs

- **Storage:** each piece stores its codes, about 3–6 KB extra, so the
  `chunks` table grows by roughly 30–60%.
- **What the database can learn:** that two pieces share a word, and roughly
  how common a code is, but never which word it is. Dropping common words
  before coding reduces this.
- **Existing data:** every stored piece must be read once, given codes and
  encrypted, company by company.

### Optional later: codes as a "keyword vector"

The codes can also be stored as a sparse vector (code → weight), so the
database can rank keyword matches itself, with rarer words counting more.
The same privacy, a little more work. The plain list is enough to start,
since our server already ranks with BM25.

---

## 4. Charts with encrypted data

Charts do three things in the database: **group** (by team, by month),
**filter** ("only Engineering"), and **add up** (sum, count, average).

### Option A: labels locked so they still group, counts left readable

Labels are encrypted so that the same label always gives the same locked
value (deterministic encryption).

| Stored | Example |
| --- | --- |
| Team (locked) | `Xq7…` (always the same for "Engineering") |
| Month | 2026-10 (readable) |
| Count | 14 (readable) |

- The database can still group by `Xq7…`, filter on it, and add the counts.
- Our server unlocks `Xq7…` back to "Engineering" just before drawing.
- Chart queries barely change.
- **The numbers are readable.** Fine for "commits per week", not for salaries.

### Option B: our server does the adding up

The database returns the **locked rows** for one chart; our server unlocks
them and does the maths itself.

- A chart reads **one table** or **one time window**, usually hundreds to a
  few thousand rows, so this takes well under a second.
- **The numbers stay protected** (salaries, budgets, revenue).
- It is a bigger change: the chart code (`app/insights/`) does the maths in
  SQL today, and that moves to Python.

### Options we would not use

- **Maths on locked numbers** (homomorphic encryption): slow, complex, not
  supported in Postgres, and only some maths works.
- **Leaving charts out of the promise:** possible as a stopgap, but the
  tables most worth protecting are exactly the ones charts read.

### The plan

| Data | Sensitive? | Option |
| --- | --- | --- |
| Activity charts (`activity_facts`: commits, issues, edits) | Counts are low-risk | **A**: locked labels, readable counts |
| Tables from documents (`doc_table_rows`: salaries, budgets, revenue) | Numbers are sensitive | **B**: server unlocks and adds up |

---

## 5. What this plan does not solve

- **Vectors stay readable**, because search by meaning needs them, and they
  can be partly turned back into text.
- **The AI model sees the passages** it answers from.
- **Hosting:** on Render the KMS credentials sit in Render's settings, so
  "no engineer can decrypt" needs the backend on Amazon first.
- **Other tables:** titles, chats, answers, cached answers, the knowledge
  graph and memory hold text too, and each needs the same treatment.

---

## 6. Order of work, if we go ahead

1. A prototype on a local database: compare today's keyword search with the
   code search on the same questions.
2. Keys (the KMS interface), encryption and word codes for `chunks`.
3. Switch keyword search to codes; decrypt everywhere `chunks` is read.
4. Encrypt existing data, company by company.
5. Charts: Option A for activity, Option B for document tables.
6. The remaining tables that hold customer text.
