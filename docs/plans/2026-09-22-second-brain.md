# Second Brain — plan, explained simply

> A beginner's guide to what we're building, why, and in what order.
> No prior knowledge of this codebase assumed.

---

## 1. What we have today, in one paragraph

Handbook is a question-answering system. A company connects its tools
(Notion, Google Drive, Slack, Linear, GitHub). We copy their documents in,
chop them into small pieces called **chunks**, and turn each chunk into a
list of numbers called an **embedding** — numbers that capture meaning, so
two pieces of text about the same thing end up with similar numbers.

When someone asks a question, we turn the question into numbers the same
way, find the chunks whose numbers are closest, and hand those chunks to an
LLM with a strict instruction: *answer only from this. If it doesn't say,
say you don't know.*

That last part is called **grounding**, and it is the thing this whole
codebase is arranged to protect. An answer we can't point at a document for
is worse than no answer.

---



## 2. What we want to add, in one paragraph

Right now the system has no long-term memory. If you ask a question today
and your teammate asks the same question tomorrow, we do the entire
expensive process again from scratch. We also can't do genuinely hard
research — the kind that needs to check five things and put them together.

A **Second Brain** fixes both: it remembers answers it has already worked
out, and it can go away and do deep research when asked.

---



## 3. The two speeds

Think of it like a person at work.

### Fast mode (the default, for normal questions)

> "How much leave do I have left?"

Before doing any work, we check: *have we answered something like this
before?* If yes, and we're confident it's the same question, we hand back
the answer we already have — along with the documents it came from, so it's
still checkable.

If not, nothing changes. The existing pipeline runs exactly as it does
today.

**Why this matters:** it's fast, it costs nothing, and it stops us hammering
Slack's and GitHub's APIs for things we already know.

### Deep mode (only when explicitly asked)

> "Research how the auth migration went — what shipped, what broke, what's left."

This one question needs several answers combined. So we:

1. Break it into smaller questions (a **plan**).
2. Answer each smaller question, checking three places in order:
  - **memory** first (free),
  - then **our indexed documents** (cheap),
  - then **live API calls** to Slack/GitHub/etc. (expensive, last resort).
3. Combine the findings into one report.
4. **Save the result into memory**, so the next person who asks gets it
  instantly.

That last step is the interesting one. It's how the system gets smarter
over time: hard work done once becomes cheap for everybody after.

---



## 4. Where the memory lives

One new database table. That's genuinely it for the core feature.

```
knowledge_notes
  ├── question     the question that was asked
  ├── embedding    its meaning, as numbers (so we can find it by similarity)
  ├── answer       what we said
  ├── provenance   which documents it came from
  ├── who/where    org, workspace, user  (see section 5)
  └── superseded_at  set when the answer goes stale (see section 6)
```

Plus a small join table, `knowledge_note_sources`, that records **which
documents each note was built from**. It looks boring. It is the most
important table in the design — section 6 explains why.

### Why not a "knowledge graph"?

The original idea was a graph: nodes and edges, entities and relationships.
We're not doing that first, because the two things we actually want from it —
*"have we answered this?"* and *"save this for later"* — are both
**similarity** questions, not **relationship** questions.

Similarity we can already do: we have the embedding model, we have pgvector,
it's one database column. A graph would need a whole new vocabulary of
entity types, a process to extract them, and rules for keeping every
relationship up to date.

We can add edges later if a real question needs them. Building them first
means guessing.

---



## 5. Who can see what

This is the part where a mistake is a privacy breach, so it gets its own
section.

There are three levels, and they already exist elsewhere in the codebase:


| Level        | Meaning                     | How it's stored             |
| ------------ | --------------------------- | --------------------------- |
| **Personal** | only you see it             | `user_id` = you             |
| **Space**    | one project's team sees it  | `workspace_id` = that space |
| **Company**  | everyone in the org sees it | both are empty              |


Two rules, both non-obvious:

**Rule 1 — a space sees only its own notes, never company-wide ones too.**
This sounds unhelpful but it's deliberate and it already applies everywhere
else here. If a private "meeting notes" space could also pull in
company-wide content, being in that space would stop meaning anything.

**Rule 2 — the dangerous one.**

We recently added per-document permissions: a Google Drive file shared with
two people is readable by only those two people, enforced when we search.

A saved note is **not** a document. It's text we generated. So that
permission check does not apply to it automatically. If we're careless, a
note is a way to read a document you were never allowed to open.

The rule:

> A note is shared with other people **only if every document it came from
> was readable by the whole space anyway.** Otherwise it's saved as private
> to the person who asked — or not saved at all.

The good news: this exact check already exists in the code as
`_is_cacheable` in `app/rag/pipeline.py`. We reuse that function rather than
writing a second one, because two copies of a security rule will eventually
disagree, and the day they disagree is the day something leaks.

---



## 6. Keeping answers from going stale

Here's the obvious objection to the whole idea:

> *A remembered answer is a wrong answer as soon as the document changes.*

Correct. And this is why `knowledge_note_sources` exists.

Every note records which documents it was built from. We already sync
documents automatically every hour. So we add one line to the sync: **when a
document changes, mark every note built on it as stale.**

```
sync finishes → these 3 documents changed
             → find every note that used them
             → mark those notes superseded
             → stale notes are never served again
```

The note isn't deleted — we set a `superseded_at` timestamp instead. That
way we can still look back and answer "why did it say that last Tuesday?",
which you will want the first time something goes wrong.

Two extra safety nets:

- **A note whose documents were all deleted is never served.** Fails safe.
- **An absolute expiry date.** GitHub is read live and has no stored
documents at all, so nothing would ever mark a GitHub-derived note stale.
Those get a short ceiling measured in hours; document-derived notes get
months.

**Without this section, the feature should not ship.** A memory with no
invalidation is just a machine for producing confident outdated answers.

---



## 7. Live tools (and why not MCP yet)

For deep research, sub-agents need to make live calls — fetch the newest
pull requests, read an active Slack thread.

We build a **tool registry**: a plain list of what can be called, what
arguments it takes, and which connector it belongs to. We already have this
shape — the GitHub agent has six tools defined in `app/rag/prompts.py`. We
move those into the registry and add the others alongside.

On top sits a **governor** that enforces:

- a maximum number of live calls per research run,
- that every call is scoped to the right company and space,
- that fast mode never calls a live tool at all.

That last rule is what keeps everyday chat fast. It's enforced by structure,
not by hoping.

### What about MCP?

MCP (Model Context Protocol) is a standard for exposing tools to *outside*
programs — Claude Desktop, Cursor, and so on. It's a good standard.

But our own agents calling our own connectors don't need a protocol between
them. They're in the same process. Adding MCP internally means adding a
server, a network hop, and a second place where "which company is this?"
gets decided — and we have a strict rule that this is decided in exactly one
place.

So: build the registry now. If someone later wants Claude Desktop to talk to
Handbook, MCP becomes a thin wrapper over that same registry — one file. The
hard part of that job is authentication, not tools, and doing it later means
doing it once, on purpose.

---



## 8. Why deep research runs in the background

Two hard limits force this.

**The LLM rate limit.** Our free tier allows 15 requests per minute. One
ordinary question already uses about six. A deep research run with five
sub-questions uses thirty to sixty. Run that inside a web request and it
will hit the limit partway through and fail, *and* it will starve everyone
else's normal questions while it does.

**The server.** We're on a small box. A request that takes four minutes
holds a worker for four minutes.

So deep research works like the weekly reports we already send:

1. You ask → we save a row and immediately reply "started".
2. A background worker picks it up and does the work.
3. When it's done, we store the report and email you a short note with a
  link to it.

The email is a **notification**, not the report. If the email fails, the
work isn't lost — the report is sitting in the app.

Every sub-question inside a research run goes through the **same** pipeline
as a normal question, with the same confidence check and the same strict
prompt. That's how multi-agent research doesn't weaken our guarantees: it
reuses the safe path repeatedly rather than inventing a new one.

---



## 9. Telling the user where an answer came from

A hard requirement: the person reading an answer must know whether it came
from memory, from their documents, from a live tool, or from the web.

We already show a small label ("pill") saying which connector answered. We
add the origin to it:

> *Answered from a previous question · 14 Sep · Google Drive*

"We worked this out before" and "we just read this from your Drive" are
different claims. The reader is the one who decides whether the older one is
good enough, and they can only decide that if we tell them.

---



## 10. The plan, in order

Each phase is useful on its own. If we stop after A, we've still shipped
something worth having.

### Phase A — Memory

The `knowledge_notes` table, the lookup before a question runs, the save
after it finishes, and the staleness marking on sync.
**Roughly one table and 200 lines.** Delivers most of the value.

### Phase B — Tool registry

Move the GitHub tools into a registry, add a budget, add the other
connectors. Mostly moving existing code.

### Phase C — Deep research

The background worker, the plan/research/combine loop, and writing results
back into Phase A's table.

### Phase D — Graph edges *(only if needed)*

Add relationships **only** if we can point at a real question that deep
research answered badly because it couldn't follow connections. Our
`activity_facts` table already records "who did what to which thing, when",
which is most of a graph already.

### Phase E — MCP *(optional)*

Expose Phase B's registry to outside tools. Only when someone actually asks.

---



## 11. What could go wrong

**The similarity threshold is the whole risk.** We need a number: how
similar must an old question be before we reuse its answer? Too low and we
serve the wrong answer confidently. Too high and memory never triggers and
we built a table for nothing.

Starting point: **0.85**. Higher than the two similar thresholds we already
use, because the cost of being wrong here is higher — a wrong memory hit is
a wrong *answer*, not just a wasted search. We tune it against real logs,
not hand-picked examples.

**The LLM rate limit blocks Phase C.** Deep research needs its own separate
LLM endpoint, configured before that phase starts, not during it.

**Per-document permissions have never been tested against a real Google
Drive folder.** Building longer-lived saved answers on top of an untested
permission system multiplies that risk. Test it live before any note is
saved as *shared*.

**Background research competes with document syncing** — they share a
worker. Slower syncs mean staler documents, and fresh documents are what
make saved answers safe. Worth measuring.

---



## 12. What we are deliberately not building


| Not building                     | Because                                                                        |
| -------------------------------- | ------------------------------------------------------------------------------ |
| A graph database (Neo4j, AGE)    | Our questions are similarity, not traversal. Postgres already does similarity. |
| Entity extraction at import time | Doubles our LLM cost against a limit we already hit.                           |
| MCP inside the system            | Adds a network hop and a second identity path for zero new capability.         |
| A second search index            | The one we have works, and two indexes drift apart.                            |
| Parallel sub-agents              | The rate limit makes parallelism pointless here.                               |


Each of these is a *later* decision, not a *never* decision — but each needs
a real problem to justify it first.