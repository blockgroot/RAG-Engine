# Paid Upgrade Path

| | |
| --- | --- |
| **Document** | 2 of 3 |
| **Subject** | What to purchase if the project leaves free and unofficial defaults, in what order, at what cost, and why those options over alternatives |
| **Audience** | Operators deciding spend against observed pain, not a catalogue of vendors |
| **Related documents** | (1) `docs/challenges-and-remedies.md`; (3) `docs/what-we-use-and-why.md`. Interfaces already exist; most upgrades are configuration. |

Prices below are indicative as of August 2026. Re-verify on the vendor’s pricing page before committing. The application is designed so that a paid substitution is a configuration change behind an existing interface, not a rewrite.

---

## 1. Principle

Do not replace the stack wholesale. Buy the **smallest paid increment that removes a measured failure**. Isolation, the confidence gate, and the grounded prompt stay. A commercial vector database or an orchestration framework is not required to leave the free tier.

Current defaults exist to keep policy text on-host and to run without a card. They impose four operational costs that money actually addresses:

1. Language-model **quota and variance** (approximately 15 requests per minute; flaky refusals).
2. **Process memory and CPU** (512 MB / 0.1 vCPU; local BGE-M3 cannot fit).
3. **Cold start** after about 15 minutes idle on Render free.
4. **Unofficial or sandboxed** side channels (DuckDuckGo rate limits; Resend sandbox; SMTP blocked).

Items that look like upgrades but do not remove those failures (Pinecone, Redis, LiteLLM, LangChain) are listed last as non-goals.

---

## 2. Recommended sequence

| Order | Purchase | Approximate cost | Pain removed |
| --- | --- | --- | --- |
| 1 | Paid Gemini API (same model family) | Usage; Flash-Lite is $0.25 / $1.50 per 1M input/output tokens | 15 rpm cap, 41 s backoff, evaluation flake, ingest contextualisation stalls |
| 2 | Render **Standard** web service (2 GB), not Starter | $25 / month compute on Hobby workspace | Cold start *and* the 512 MB ceiling that made ingest fatal |
| 3 | SendGrid (or verified Resend domain) | Free Single Sender, or low tens of dollars with a domain | Magic-link and approval mail that never arrives |
| 4 | Tavily **Project** if web fallback is used in production | $30 / month (4,000 credits) | DuckDuckGo throttling; advisory web evaluation |
| 5 | Jina remote embed + rerank, *or* a host with at least 2 GB for local models | Jina typically $0.05 / 1M tokens after a one-time 10M grant; or included in (2) | Multi-GB weights on a 512 MB box; first-request model load |
| 6 | Optional: `LLM_AUX_MODEL` on Flash-Lite, answers on a stronger paid model | Flash plus a higher Gemini or OpenAI/Anthropic tier | Bookkeeping cost versus answer quality |

Do not start with a dedicated vector database, Redis, or a new RAG framework. Those add operations without fixing quota, RAM, cold start, or mail.

---

## 3. Language model

### 3.1 Current problem

Development and deploy have used an OpenAI-compatible free endpoint (Gemini Flash-Lite class). Observed effects: a hard 15 requests per minute, server-requested waits around 41 seconds, golden-set refusals of answerable questions with the correct chunk retrieved, and ingest contextualisation dropping prefixes under concurrency 8. Free-tier traffic may be used to improve the vendor’s products; paid Gemini states that it is not.

The client is already the official `openai` package. Changing host is `LLM_MODEL`, `LLM_BASE_URL`, and the API key.

### 3.2 Recommended: paid Gemini, same wire format

| Item | Detail |
| --- | --- |
| Product | Gemini API paid tier (`gemini-3.1-flash-lite` or successor) |
| List price (August 2026) | $0.25 per 1M input tokens, $1.50 per 1M output; cached input $0.025 per 1M |
| Why this first | No code change. Same model family as today, so prompt behaviour is comparable. Paid tier adds rate-limit headroom, context caching (the grounded prompt is about 96% a fixed prefix), and a data-use policy suitable for customer policies. |
| What it mitigates | Ingest 429s, evaluation retries, chat stalls, and the need to set `INGEST_CONTEXTUAL_CONCURRENCY` to 1–2 as a quota workaround. |

A typical grounded prompt is about 2,300 tokens, of which about 2,200 are a byte-identical instruction prefix. Enabling provider caching needs no prompt restructuring provided CONTEXT and QUESTION remain last. That saving exists only on a paid host.

**Illustrative monthly cost.** 10,000 answered questions × about 2,500 input and 400 output tokens ≈ 25M input + 4M output ≈ **$6 + $6** on Flash-Lite before caching, plus ingest prefixes. This is budget-tier spend, not a second payroll.

### 3.3 When to choose something else

| Option | When it is better | Why not first |
| --- | --- | --- |
| Gemini 3 Flash / 3.1 Pro | Answer quality or long-context reasoning is the complaint, not rpm | Two to ten times the token price; keep Flash-Lite as `LLM_AUX_MODEL` |
| OpenAI (GPT-class) | The vendor’s function-calling or ecosystem is specifically required | Another account and prompt regression; the interface already allows it later |
| Anthropic Claude | Native prompt caching or a particular refusal style is required | Native features imply a LiteLLM (or native) backend behind `LLMProvider`; do not add LiteLLM until that feature is required |
| Self-hosted vLLM | Data must not leave the customer network even to Gemini | GPU capital and operations; correct for the eventual self-hosted image, not for the current Render box |

Do not buy LiteLLM in order to support many providers. The OpenAI client already does. Buy LiteLLM only when a native-only feature is an accepted requirement.

---

## 4. Compute and memory (Render)

### 4.1 Current problem

Free plan: 512 MB RAM, 0.1 vCPU, spin-down after inactivity, five-second health check. Document 1 records how that ceiling turned ingest bugs into an unattended crash loop. Starter ($7 / month) is **still 512 MB**. It removes idle spin-down and raises CPU to 0.5; it does **not** create room for local BGE-M3 and the reranker.

### 4.2 Recommended: Standard instance, Hobby workspace

| Item | Detail |
| --- | --- |
| Product | Render web service **Standard**: 2 GB RAM, 1 CPU, $25 / month |
| Workspace | Hobby ($0) is sufficient until SOC 2 documentation or team features are required (Pro workspace $25 / month extra) |
| Why Standard, not Starter | Starter keeps the memory class that made ingestion fatal. Standard is the first tier with 2 GB, which is the minimum for local models *or* comfortable remote ingest plus HTTP. |
| Why not Render Postgres | A Render-managed database prompted for payment information even on a free database plan. External Postgres (Supabase session pooler, port 5432) remains the correct store. |

**Why not Fly.io, Railway, or a VPS first.** Any always-on 2 GB host removes the same pain. Render is already the Blueprint target; changing platform is a migration, not a configuration change. Revisit only if region or Docker behaviour is the complaint.

**Region.** Compute must sit near the database. A US-West API and `ap-south-1` Postgres cost about 250 ms per round trip; `GET /me` multiplied that by query count. `render.yaml` specifies `singapore` for Mumbai. Region cannot be changed in place; a new service is required. Paid CPU does not fix distance.

Local embeddings on Standard are possible, but the first request still loads multi-GB weights unless warmup stays enabled. For a 2 GB box, **remote Jina embed and rerank** (section 6) is safer than loading both models.

---

## 5. Email delivery

### 5.1 Current problem

Render free blocks outbound SMTP. `EMAIL_SENDER=smtp` never reaches credentials. Resend over HTTPS works, but the sandbox sender (`onboarding@resend.dev`) delivers only to the Resend account owner. Approval mail to the owner succeeds; magic links to anyone else fail while the API reports success.

### 5.2 Recommended: SendGrid Single Sender, then a domain

| Option | Cost | Fit |
| --- | --- | --- |
| SendGrid Single Sender Verification | Free | One `EMAIL_SMTP_FROM` may reach any recipient without DNS. Weaker alignment (no SPF/DKIM on a custom domain). Correct next step. |
| Resend with a verified domain | Domain plus Resend plan | Equivalent once DNS exists; do not stay on the sandbox. |
| Render paid plus SMTP | Compute plus Gmail or SendGrid SMTP | Unnecessary: HTTPS senders already work on free compute. Paying Render does not require returning to SMTP. |

Set `EMAIL_SENDER=sendgrid` and `EMAIL_SENDGRID_API_KEY`. This is independent of language-model spend and unblocks signup, invites, and login.

---

## 6. Embeddings and reranking

### 6.1 Current problem

Local BGE-M3 and `bge-reranker-v2-m3` are correct for self-hosting and for keeping text on the machine. They do not fit 512 MB. Remote backends exist; a previous paid embed host (DeepInfra) returned `402`. Jina is the documented remote pair (`EMBEDDING_BACKEND=remote`, `RERANKER_BACKEND=remote`).

### 6.2 Recommended if compute stays small: Jina

| Item | Detail |
| --- | --- |
| Product | Jina embeddings plus `jina-reranker-v3` (already the remote default) |
| Cost | One-time grant of about 10M tokens; then typically $0.05 per 1M tokens shared across embed and rerank |
| Why Jina over OpenAI `text-embedding-3` | Already wired; one key covers embed and rerank; list price is low at this volume; no second vendor for rerank (Cohere Rerank is a separate bill). |
| Why not as the long-term default | Document text leaves the host. For a customer-owned image, return to local BGE-M3 on a machine that can hold it. |

**Cohere Rerank** is a quality alternative. It is a new vendor, a new interface mapping, and policy chunks would leave the host twice if embeddings stayed elsewhere. Use it only if measured ranking quality on the golden retrieval set justifies it after Jina is in place.

**OpenAI embeddings** require a schema and dimension change if the width is not 1024, then a full re-ingestion. Do not switch embedding family casually.

If Render Standard (2 GB) or a self-hosted box is available, **keep local BGE-M3** and disable remote. That preserves the original data-residency goal and avoids re-ingestion.

---

## 7. Web search

### 7.1 Current problem

DuckDuckGo via `ddgs` is keyless and unofficial. It rate-limits aggressively. Golden-set web cases are advisory because empty results correctly degrade to the internal fallback. That is acceptable for a rare external-entity path; it is not acceptable if customers rely on web answers.

### 7.2 Recommended: Tavily, behind the existing interface

| Plan | Credits per month | Price | Role |
| --- | --- | --- | --- |
| Researcher | 1,000 | Free | Confirm the integration (`WEB_SEARCH_PROVIDER=tavily` plus key) |
| **Project** | 4,000 | **$30** | Production default at this volume (basic search ≈ 1 credit) |
| Bootstrap | 15,000 | $100 | Only if web is a common path |
| Pay as you go | Usage | $0.008 per credit | Spiky load without a monthly commitment |

**Why Tavily over Brave, SerpAPI, or billed Google Programmable Search.** Tavily is already named in configuration as the production swap. It is built for language-model tool results (clean snippets, one call). SerpAPI and Google Search are general SERP products: more operations, more HTML noise, and usually higher cost per useful snippet. Brave is a reasonable second if Tavily’s data-use terms are unacceptable; it is not wired today.

Web search remains a **labelled fallback**, not a second knowledge base. Paying Tavily does not change the tool description (external named entities only).

---

## 8. Database and hosting adjacency

Supabase (or Neon) session-pooler Postgres remains appropriate. Upgrade the **database plan** when connection limits, disk, or backups are the incident—not because RAG “needs a vector database.”

| Temptation | Why to wait |
| --- | --- |
| Pinecone / Weaviate Cloud | Isolation would split across vendors; document 3 explains why pgvector is the store. Buy ANN only at millions of chunks per tenant. |
| Render-managed Postgres | Card requirement observed; session-pooler semantics already match `register_vector`. |
| Redis / Celery | Jobs already use `SKIP LOCKED`. Redis does not fix quota, RAM, or mail. |

---

## 9. What not to buy yet

| Product | Common pitch | Why it does not earn a place in this sequence |
| --- | --- | --- |
| LiteLLM | Multi-provider routing | Already achieved with `base_url`. Add only for native Anthropic caching. |
| LangChain / LlamaIndex | Faster RAG features | Would own the gate and prompt. Contradicts document 3. |
| Cohere embed plus rerank bundle | Best-in-class retrieval | Data residency; extra vendor; dimension and schema change. |
| Pinecone | Managed vectors | Second isolation path; Postgres already holds vectors. |
| Redis | Faster cache | Query-path work was eliminated; `query_answer_cache` is Postgres. |
| Always-on LLM query rewrite | Better than SymSpell | Permanent latency; buy model quota first, then re-measure. |
| Dual-LLM NLI (Phase 20) | Citation verification | Cost and latency after token logs justify it—not an early purchase. |

---

## 10. Illustrative monthly bill (small production)

Assumptions: one always-on API, paid Flash-Lite, SendGrid free sender, Tavily Project, Jina after the welcome grant, existing Supabase.

| Line | Monthly |
| --- | --- |
| Render Standard (2 GB) | $25 |
| Gemini Flash-Lite (order-of-magnitude Q&A plus ingest) | about $10–30 |
| Tavily Project | $30 |
| Jina embed/rerank | about $0–10 at this volume |
| SendGrid Single Sender | $0 |
| **Total** | **roughly $65–95** |

Starter-only ($7) plus paid Gemini would still leave the 512 MB ingest hazard. Skipping Tavily until web is customer-visible saves $30. Skipping Jina if Standard hosts local models saves the Jina line but spends RAM and warmup time.

A self-hosted customer image inverts this: spend on GPU or RAM inside their network, keep BGE-M3 local, and point `LLM_BASE_URL` at their endpoint. That is the original product goal; the table above is only for the current shared Render deployment.

---

## 11. How to apply an upgrade

All of the following are environment changes. No retrieval or isolation logic should move.

1. **LLM.** Paid Gemini key; `LLM_BASE_URL` to Google’s OpenAI-compatible endpoint; optional `LLM_AUX_MODEL` for rewrite and ingest.
2. **Compute.** New Render service at Standard (region next to Postgres); update Vercel `API_PROXY_TARGET`; delete the free instance. Region is immutable.
3. **Mail.** `EMAIL_SENDER=sendgrid` plus key; verify the from-address.
4. **Web.** `WEB_SEARCH_PROVIDER=tavily` plus `WEB_SEARCH_API_KEY`.
5. **Embed/rerank.** Either `EMBEDDING_BACKEND=remote` and `RERANKER_BACKEND=remote` with one Jina key, or keep local on a host with at least 2 GB. Changing embedding *model family* requires `EMBEDDING_DIM`, schema, and re-ingestion.

After (1), re-run the golden path set without treating free-tier retries as the reliability story. After (5), re-run retrieval-rank evaluation if the embedding model changed.

---

## 12. Decision rule

Pay when an item in document 1 is still the live incident **and** the row in section 2 names that incident. Do not pay to make the architecture look more like a reference RAG diagram. The interfaces are already the upgrade mechanism.
