# Implementation Challenges and Remedies

| | |
| --- | --- |
| **Document** | 1 of 3 |
| **Subject** | Failures encountered while building and operating the system, and the remedies that remain in the code |
| **Audience** | Engineers repeating a similar stack, and reviewers asking why a bound or gauge exists |
| **Related documents** | (2) `docs/paid-upgrade-path.md`; (3) `docs/what-we-use-and-why.md`. Incident-level detail remains in `CLAUDE.md` §4. |

This is not a complete diary of every defect. It groups failures by **class**, because the reusable lesson is the class, not the ticket.

---

## 1. How to read this document

Most serious incidents shared a shape:

1. An **unbounded input or hidden cost** (memory, round trips, fan-out).
2. A **wrong gauge** (peak RSS treated as current; job age treated as liveness).
3. A **blast-radius amplifier** (requeue without an attempt limit; a global rate-limit bucket).

Fixes that only addressed the proximate trigger were real and were kept. They were not sufficient until the amplifier was closed. The ingest outage on Render free is the canonical example.

---

## 2. Ingest memory and the Render 512 MB ceiling

### 2.1 What was observed

On Render’s free instance (512 MB, 0.1 vCPU), the service entered a tight loop: `Instance failed` every few minutes. `ingestion_jobs` showed a row stuck `running` with `processed_documents = 0`. Login and chat failed with it, because the API process is also the ingest worker.

### 2.2 Why several plausible fixes were not enough

Each of the following was a genuine defect and was fixed. None alone stopped the loop, because each left the process near the memory ceiling.

| Hypothesis | What was true | Why it was not the whole story |
| --- | --- | --- |
| Unbatched remote embeddings | `LocalEmbeddingProvider` batched; `RemoteEmbeddingProvider` sent an entire document in one HTTP call | Necessary. A large Notion page could still OOM. Jobs still died in `preparing` before embed. |
| Unbounded Notion fetch | Block walk had no depth or size cap; `max_document_chars` ran *after* the string was built | Necessary. Fan-out of child API calls and an unbounded string occurred pre-embed. |
| Tokenizer import at boot | `transformers.AutoTokenizer` imported at module load (~7 s), tripping the 5 s health check | Necessary for cold start. Deferring the import moved a ~325 MB allocation from boot into ingest; it did not remove it. |
| BGE-M3 tokenizer as the split oracle | Exact token counts for 256-token chunks loaded a multi-hundred-MB tokenizer | Dominant floor: after `chunk_text` on a large doc, RSS was ~429 MB in a 512 MB Linux container (~83 MB headroom). Almost any other spike then killed the process. |

Mac measurements (~611 MB) overstated the Linux deploy image. The number that matters is `docker run --memory=512m` against `requirements-deploy.txt`.

### 2.3 Remedies that remain

- **Batch remote embeddings** at `EMBED_BATCH_SIZE` (default 16), same as local.
- **Bound the Notion walk** with a shared character budget (`INGEST_MAX_DOCUMENT_CHARS`). Stop pagination when exhausted; append a truncation marker. Charge a block’s own text, not only its children.
- **Heuristic token counts by default** (`CHUNK_TOKEN_BACKEND=heuristic`). Exact BGE-M3 counting is opt-in (`hf`) for hosts with RAM. Counts only choose split points; the embedder retokenises server-side. Golden-corpus chunks stay under the 256-token budget and err small.
- **Character backstop** (`CHUNK_MAX_CHARS=4000`). A Drive export contained a 48 KB whitespace-free base64 blob. Linguistic splitters emitted one chunk; the embed API returned `INPUT_TOKEN_LIMIT_EXCEEDED`. Base64 is ~1 token per character, so an estimator cannot bound it.
- **Contextualisation caps:** defer enrichment until after a successful store; skip documents with more than 200 chunks; concurrency 2 on a 15 rpm endpoint.
- **Admission gate:** skip claiming a job when current RSS ≥ `INGEST_MAX_RSS_MB` (default 400). Fail *open* if RSS cannot be read.

### 2.4 The amplifier: unlimited requeue

`requeue_interrupted_running()` returned every orphaned `running` job to `queued` with **no attempt limit**. A job that killed the process was claimed again on the next boot. `reap_stuck()` could not help: the process died inside the reap interval, and the next boot undid a reap.

**Remedy:** `ingestion_jobs.attempts` increments at **claim** time (a crash never reaches a completion counter). After `INGEST_MAX_JOB_ATTEMPTS` (default 3), the job is marked `failed` instead of requeued. A future ingest bug costs one failed job, not an outage. Immediate recovery for a stuck row: set `status='failed'` so requeue will not pick it up.

This bound is kept regardless of the next causal bug.

---

## 3. Wrong gauges

A recurring error was using a real number that answered the wrong question.

| Gauge used | What it actually measures | What was needed | Remedy |
| --- | --- | --- | --- |
| `ru_maxrss` | Lifetime peak RSS; never falls | Current RSS, so ingest can resume after a spike | `/proc/self/statm` on Linux; `ps` on macOS; fail open |
| `started_at` vs reap timeout | How long the job has been running | Whether the job is **silent** | `progress_at` on every progress write; reap on `coalesce(progress_at, started_at)` |
| Socket peer as client IP | The proxy, identical for every user behind Vercel → Render | A stable per-client identity for auth rate limits | `resolve_client_ip`: pinned header, then edge-written headers, then leftmost `X-Forwarded-For` last (caller-controlled) |

The memory gate with `ru_maxrss` did not throttle ingest; it **disabled** it for the rest of the process lifetime after a single peak. The reap rule marked healthy, slow ingest (large folder, 15 rpm contextualisation) as `failed` while the worker continued. The auth limiter, keyed on `request.client.host`, gave every user one bucket: enumeration was unbounded, and one noisy client returned 429 to all logins.

---

## 4. Unbounded walks and listings

The Notion fetch bound was one instance of a class: **walks that stop on depth but not on breadth, or that act on a single unverified list.**

| Surface | Failure | Remedy |
| --- | --- | --- |
| Google Drive folder walk | Depth capped; folder count not capped. Each folder is a `files.list`. The Sources “check for changes” path used the same walk. | `GOOGLE_MAX_WALK_FOLDERS` (500) and `GOOGLE_MAX_DOCUMENTS` (2000); log truncation rather than a silent partial index. |
| `list_documents()` as delete authority | One listing computed `removed = stored − live_ids`. A transient Notion search lag after an edit reported “1 new · 11 removed” on a connection that still had those pages. `ingest_source` would have deleted them. | `_sanitize_removals`: refuse to delete more than 50% of known documents when at least 5 are stored. Adds and updates still proceed. Tiny corpora (1→0) are allowed. |
| First sync empty listing | Connect → ingest within seconds listed an index page and omitted shared children. A second listing moments later found them. | Retry once after 5 s only when nothing is stored and the first listing returned ≤ 1 page. Keep the larger of the two listings. |

GitHub commit diffs were bounded on the same principle: cap files and patch bytes, and **mark** truncation. A silently shortened diff lets the model answer from half the evidence.

User-supplied strings (`company_name`, workspace name, folder URL, email) had no length check. Oversize values were rejected, not truncated: a silent shorten disagrees with the request; a truncated URL is a broken URL.

---

## 5. Query-path latency (work that did not need doing)

“Every page takes ~10 seconds” was attributed to region, then to too many logical queries on `/me`. Both mattered less than a hidden catalogue lookup.

`register_vector` ran on **every pool checkout**. It is not local: it fetches type OIDs from `pg_type` (several round trips). Ten trivial `SELECT 1` calls produced 58 round trips. At ~250 ms API→database, `/me` spent seconds on lookups. **Remedy:** register once per physical connection (`_register_vector_once`). Steady state is one round trip per query. Count round trips wrapping `Cursor.execute`, not `Connection.execute`—`TypeInfo.fetch` uses its own cursor.

Further behaviour-preserving cuts:

1. **Corpus text was refetched on every question** to rebuild a SymSpell dictionary that is cached for the process lifetime. `normalize()` now takes a thunk, resolved only on cache miss. The cache is an LRU of organisations (`QUERY_NORM_CACHE_MAX_ORGS`).
2. **Vector and keyword searches ran serially**, including per sub-question. Independent searches now run on a pool of four workers, below `DB_POOL_MAX_SIZE`. Results are reassembled by index, not completion order (RRF is order-sensitive). Gain is negligible on a toy corpus and material at hundreds of chunks.
3. **Keyword search was unbounded.** Postgres now keeps `KEYWORD_CANDIDATE_LIMIT` rows by `ts_rank` before joining embeddings.
4. **The question was embedded twice** on the common path (reuse check, then retrieve). A `known_vectors` map keyed by text removed the duplicate (~38 ms local BGE-M3).

Redis was considered and not added. The costs were unnecessary work, not a slow cache.

---

## 6. Authentication, cookies, and GitHub connect

| Incident | Cause | Remedy |
| --- | --- | --- |
| Login appeared to succeed then immediately log out | `SameSite=Lax` cookie set on `onrender.com` is not sent from `vercel.app` | Browser origin is `/api`; Next.js rewrites to FastAPI. OAuth callbacks use the frontend host. Do not set `NEXT_PUBLIC_API_BASE_URL` to the Render URL. |
| SMTP “network unreachable” | Render free firewalls ports 25/465/587 | HTTPS senders (`sendgrid` or `resend`). Do not debug Gmail passwords for `Errno 101`. |
| Magic links never arrived except to the operator | Resend sandbox delivers only to the Resend account owner | SendGrid Single Sender, or a verified domain. `send_*_email_safe` swallows provider errors, so the UI can report success. |
| GitHub connect 422 on GitHub’s own redirect | Callback required `code` and `state`. App install redirects with `installation_id` + `setup_action` | Optional params; explicit branches. No `code` → finish-connect message. `code` without `state` → refuse (no trustworthy tenant). Non-GitHub providers still 400. |
| Workspace GitHub showed the company repos | Install id from a company org was bound to a personal space; `_pick_installation` fell back to `installations[0]` | Prefer user account; do not fall back; reject a workspace install id equal to the org-wide row. Compare install ids, not account type. |
| Spoofed `installation_id` | GitHub documents that the query parameter can be forged | Verify against `GET /user/installations`; persist identity from that response only. |

Mail scanners prefetch GET links. Signup approve/reject is GET (confirmation page) then POST (mutation). A GET-mutates link would let Outlook Safe Links act for the operator.

---

## 7. Local development and model load

Loading BGE-M3 and the reranker twice (policy agent and workspace agent as FastAPI `Depends` on every chat request) could freeze a 16 GB Mac. **Remedy:** lazy agent selection and process-wide singleton embedder/reranker. Avoid `uvicorn --reload` during demos. GitHub chat does not load those models. `RETRIEVAL_RERANK_ENABLED=false` is the kill-switch.

Models now warm on a daemon thread at API startup so the first user after restart does not pay the load inside their request. The test suite disables warmup.

---

## 8. Retrieval and grounding (behaviour, not outages)

These did not take the site down. They produced wrong or missing answers and were fixed without weakening the gate.

| Observation | Remedy |
| --- | --- |
| Typos buried the correct chunk (mid-pool) on standalone questions | Corpus-vocab SymSpell on the retrieval key only; edit distance 1; skip capitalised OOV so web entities are not rewritten |
| Follow-up reuse could not separate “same fact” (~0.63) from “adjacent topic” (~0.67) | Reuse threshold 0.72 (conservative). Reused chunks still pass the 0.35 gate |
| Similarity 0.35 cannot tell “answers” from “on-topic but unanswered” | Gate stays a noise filter; the prompt refuses related-but-unanswered cases. Golden set: no false negatives; unanswerables caught by the prompt |
| Indirect prompt injection via retrieved text | Fence untrusted content; sandwich reminder; narrow heuristic scrub. Partial mitigation; not dual-LLM quarantine |

---

## 9. Schema and process hazards

`ALTER TABLE ... ADD COLUMN` for `workspace_id` was placed **before** `ingestion_jobs`’s `CREATE TABLE` in `schema.sql`. Re-applying to an already-migrated database succeeded; a fresh CI database failed (`relation "ingestion_jobs" does not exist`). Every new cross-table column must follow that table’s `CREATE TABLE`, and schema changes should be verified on an empty database.

`org_id` on `org_signup_requests` uses `ON DELETE SET NULL`. Test cleanup that deletes an organisation must delete signup-request rows separately.

---

## 10. Lessons worth keeping

1. **Measure RSS on the target platform against the hard limit** before debating call sites. Three ingest fixes shipped against plausible hypotheses before the tokenizer floor was measured in a 512 MB Linux container.
2. **Prefer a bound that remains useful if the diagnosis is wrong.** The job attempt cap is valuable because the next ingest killer will not take down HTTP.
3. **Count the operation, not the latency, when the cost is a network hop.** Local timing would never have shown `register_vector`’s 5.8 round trips per query.
4. **Do not act on a single unverified listing** when the action is mass deletion.
5. **A real metric can still be the wrong one.** Peak RSS, job start time, and socket peer were all accurate and all answered the wrong question.

Paid substitutions that remove quota, RAM, and cold start without changing this architecture are the subject of document 2. Component rationale is document 3.
