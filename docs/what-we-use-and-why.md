# Design Choices: What We Use, Why We Use It, and How It Works

| | |
| --- | --- |
| **Document** | 3 of 3 |
| **Subject** | Current architecture, component selection, and retrieval strategy |
| **Audience** | Engineers and reviewers evaluating whether to retain, replace, or commercially upgrade a component |
| **Related documents** | (1) `docs/challenges-and-remedies.md`; (2) `docs/paid-upgrade-path.md`. Operational configuration remains in `ARCHITECTURE.md` and `CLAUDE.md`. |

---

## 1. Purpose of the system

The product is a **multi-tenant retrieval-augmented generation (RAG) platform** for company question-and-answer.

Each tenant connects **Notion, Google Drive, Slack, Linear and GitHub**. Employees ask in the web app or in Slack and receive answers **grounded in that tenant’s own material**, limited to what they may open in the source tool. GitHub is answered from live API reads and is not embedded. Charts add up stored rows in SQL: recorded tool activity, and tables and paragraph figures read from documents and uploads. Agent mode drafts writes into the connected tools, each approved on a card and sent through an MCP server an admin added (`app/actions/`). Current product status is `docs/handbook/PRODUCT_STATUS.md`.

The intended deployment is a **self-hosted Docker image** that an enterprise can run inside its own infrastructure. The code's defaults are local (BGE-M3 and its reranker in-process). The hosted service and the self-hosted package (`deploy/selfhost/docker-compose.yml`) both default to remote Jina embeddings and reranking and to Claude for answers, because those boxes do not hold multi-GB weights. Identifiers (Aadhaar, PAN, card, mobile and others) are masked before any text leaves for those services (`app/privacy/`).

RAG is used in preference to fine-tuning because policies are facts that change. A policy update is a re-ingestion, not a retraining cycle. The answer names the page it used: one page as a line under the answer, two or more as a number on the sentence. The link is the address stored at sync.

---

## 2. Design principles

Three constraints govern every selection:

1. **Tenant isolation is enforced in the query.** Every read is scoped by `org_id` and, where applicable, `workspace_id`. A workspace query must not blend org-wide policy rows.
2. **Grounding takes precedence over fluency.** An unsupported but fluent answer about leave or pay is worse than a refusal. Retrieval may change which chunks reach the model; it must not weaken the confidence gate.
3. **The runtime remains self-hostable and dependency-light.** One PostgreSQL instance, thin official SDKs, and in-process models are preferred. Each capability is an interface with a factory, so a later commercial substitution is a configuration change rather than a rewrite.

Components that violated (1) or (3) for a modest quality gain were rejected. The resulting stack is deliberately conservative relative to LangChain, Pinecone, and Cohere.

---

## 3. Stack summary

### 3.1 Retrieval and generation

| Layer | Selection | Rationale |
| --- | --- | --- |
| Language model | Official OpenAI client; any OpenAI-compatible endpoint. Production answers on Claude Sonnet 5.5 or Haiku 5.5 (the picker); background work on a Gemini aux model | Provider and host are swapped by configuration. LiteLLM and LangChain are not required. |
| Embeddings | BGE-M3 (1024 dimensions) locally by default; remote Jina on the hosted and self-hosted deploys (`jina-embeddings-v3` in the self-hosted package, also 1024) | Strong retrieval quality. Local inference is free of API cost and can retain text on-host. |
| Reranker | bge-reranker-v2-m3 locally; remote `jina-reranker-v3` with the remote backend | Same family as the embedder in each case. One Jina key covers both. |
| Database | PostgreSQL 17 with pgvector 0.8+ (`halfvec(1024)`, HNSW) | Vectors, full-text search, identity, jobs, and conversation state share one store. |
| Rate limits | Token buckets in Redis (Valkey) when `REDIS_URL` is set, else per process | Redis holds rate limits only. Nothing else depends on it. |
| Keyword search | Full-text filter plus Okapi BM25 | Recovers exact terms that cosine similarity under-weights. |
| Fusion | Reciprocal Rank Fusion (k = 60) | Rank-based merge; cosine and BM25 scores need not be calibrated. |
| Chunking | 256 tokens with 40-token overlap | Approximately one policy proposition per chunk. |
| Contextual enrichment | Deferred LLM prefixes at ingest | Situating context enters both the vector and keyword indexes, with no query-time cost. |

### 3.2 Query path and product layer

| Layer | Selection | Rationale |
| --- | --- | --- |
| Spelling normalisation | SymSpell against organisation vocabulary (edit distance 1) | Corrects typos without an LLM call on every request. |
| Grounding | Cosine gate of 0.35 plus a strict prompt | Inexpensive noise filter, then a semantic sufficiency check. |
| Conversation memory | PostgreSQL turns and an incremental summary | Follow-up questions are rewritten into standalone queries. |
| Web fallback | DuckDuckGo via tool-calling; labelled output | No API key. Restricted to named external entities. |
| Content sources | Notion, Drive, Slack, Linear (indexed); GitHub live; Forms labels only | Sharing from Drive, Slack, Linear and GitHub is applied in the query. Notion is per space. |
| API and interface | FastAPI and Next.js 15 | Organisation identity is taken only from the signed session cookie. |
| Charts | Model fills a spec; SQL adds up stored rows; ECharts on the web, matplotlib PNG in Slack | The model never emits a number. Tables in documents and uploads are read without AI (openpyxl, pdfplumber); figures in paragraphs are listed by the background model and checked against their quotes. |
| Agent mode (writes) | MCP servers an admin adds; the five connectors are presets (`app/actions/mcp_presets.py`) | No hand-written per-tool actions. Each person signs in with their own account; every write waits for their approval on a card. |
| Identifier masking | Format and check-digit detectors (`app/privacy/`) | Masks identifiers before text reaches the models, Jina or the guard; the reply is restored for the reader. |
| Injection guard | Llama Prompt Guard 2 or gpt-oss-safeguard (`app/guard/`), on its own account (`GUARD_API_KEY`, `GUARD_BASE_URL`) when set | Scores untrusted text (`GUARD_MODE`, off by default). A flagged question is logged, never refused. Its calls do not spend the answering or background model's quota. |
| AI usage limits | Tokens a minute per model, and dollars a day (`app/security/ai_spend.py`) | Input and output tokens apart, per person and per company (`AI_TOKEN_LIMITS`); one dollar wallet across models (`AI_*_DOLLARS_PER_DAY`). |
| Evaluation | Golden path checks, retrieval rank, optional RAGAS | Continuous integration remains fast; LLM-as-judge runs on a slower cadence. |

---

## 4. End-to-end flow

### 4.1 Ingestion

The source adapter fetches a changed page, thread, or issue. Text is preprocessed, chunked, optionally prefixed with context, embedded in batches of 16, and stored with `org_id` (and `workspace_id` when the row belongs to a space). Sync is partitioned by provider, so a Google synchronisation cannot delete Notion documents. Slack threads and Linear issues use the same pipeline. GitHub is not ingested.

### 4.2 Question answering

1. Conversational follow-ups are rewritten into a standalone question.
2. SymSpell normalises the retrieval key only.
3. The question is embedded once.
4. Previous-turn chunks are reused if cosine similarity is at least 0.72; otherwise vector search and BM25 are fused with RRF, reranked, and truncated to five chunks (`RAG_TOP_K`). When several documents score close to the best, up to ten passages are read, one per document first (`RAG_WIDE_MAX_HITS`); each kept hit also brings one neighbouring chunk (`RAG_NEIGHBOR_CHUNKS`).
5. Compound questions may be decomposed. At most one recovery expansion is permitted.
6. The confidence gate requires a best cosine of at least 0.35.
7. Generation uses one of three modes: explicitly supported, related but not explicit, or no supporting evidence.
8. A web-search tool is offered only if internal evidence remains insufficient.
9. The model may mark a sentence with `[n]`. A number is kept only when a retrieved document sat at that block. The link is `documents.source_uri`, saved at sync. One document is named under the answer; two or more keep a superscript and the same list. The list is stored on the turn and shown again when that chat is opened. The response also carries `source` of `policy`, `web`, or `none`.

### 4.3 GitHub

The GitHub path does not retrieve embeddings. An answer is composed from a single tool round. Absence of a tool call, a refused repository, or a failed read returns a fixed fallback. Grounding is therefore structural rather than threshold-based.

---

## 5. Language model

The implementation uses the official OpenAI Python client. `LLM_ADAPTER` names the provider from a fixed list (Gemini, Groq, OpenAI, Anthropic, OpenRouter and others, or `custom` with `LLM_BASE_URL`), `LLM_MODEL` is the model, and a boot check refuses an unknown adapter or model. Claude is reached through Anthropic's OpenAI-compatible endpoint with the same client.

In production (since 11 Nov 2026) Claude Sonnet 5.5 answers by default and Haiku 5.5 is the other pick; the picker offers those two only (`app/llm/catalog.py::CLAUDE_MODELS`), and every pickable model must be priced. Background work (rewrite, recovery, summarisation, ingest context, classifiers, tables, graph) runs on a Gemini aux model (`LLM_AUX_ADAPTER`, `LLM_AUX_MODEL`), which is never pickable and never charged. Admins can still bring their own provider (`app/llm/org_model.py`).

Reasoning is off on every call, or at the lowest effort a model accepts (`app/llm/routed.py`): Anthropic gets thinking disabled, Sonnet 5.5 the form it names, Gemini `reasoning_effort: minimal`. Hidden reasoning spent the token cap and pushed the `MODE:` tag off the front of the reply, which silently skipped the audit.

Every provider either factory builds is wrapped in `MaskingLLMProvider` (`app/llm/masking.py`), so identifiers are replaced with placeholders before a call and restored in the reply.

**LiteLLM was not adopted.** The required capability is the OpenAI wire format, which most hosts already expose. LiteLLM would add a dependency for native features that are unused today. Should Anthropic prompt caching become necessary, a LiteLLM-backed class can be introduced behind the existing `LLMProvider` interface.

**LangChain and LlamaIndex were not adopted as the orchestrator.** Those frameworks own the chain, retriever, prompt, and document type. This system already owns those concerns. Using LlamaIndex solely to read Notion would import a large transitive graph in order to unwrap framework `Document` objects. The official `notion-client` returns ordinary dictionaries.

Changing provider is therefore a configuration change. Rate limits on a free endpoint are a hosting matter; the interface already permits a different host.

---

## 6. Embeddings

The embedding model is `BAAI/bge-m3` (1024 dimensions, L2-normalised). The local backend uses `sentence-transformers`; a remote OpenAI-compatible HTTP backend implements the same interface. Deployments with a 512 MB memory ceiling typically use the remote backend so that multi-gigabyte weights are not loaded in-process.

| Alternative | Assessment |
| --- | --- |
| OpenAI or Cohere embedding APIs | Commercial cost; document text leaves the host. Unsuitable as the default. |
| MiniLM | Adequate for demonstrations; weaker on long policy prose. |
| E5 / GTE | Strong retrieval, but no matching cross-encoder in the same family. |
| BGE-M3 | Local, multilingual, designed for retrieval, and paired with the chosen reranker. |

At ingest, chunks are embedded in batches of 16. At query time the question is embedded once; that vector is reused for the reuse check and for vector search. Vectors are stored as `halfvec(1024)` (half precision, a third of the old index size). A model change requires updating both `halfvec(1024)` in the schema and `EMBEDDING_DIM`, followed by re-ingestion; `scripts/check_embedding_config.py` refuses a mismatch. Same width is not same vectors: moving between BGE-M3 and Jina also needs re-ingestion.

---

## 7. PostgreSQL and pgvector

A single PostgreSQL instance stores users, OAuth tokens, documents, 1024-dimensional `halfvec` vectors with an HNSW cosine index, full-text search, conversations, and ingestion jobs. There is no answer cache: every question is answered fresh.

`chunks` has one partition per organisation (created and dropped by triggers on `organizations`) and row-level security. Pooled connections act as the `handbook_app` role and see only the rows of the organisation passed to `get_connection(org_id=...)`. Queries still filter on `org_id`, which is also what prunes to the partition. Every pooled connection sets `hnsw.iterative_scan = strict_order`, because tenants share one index: without it, at 1.1M vectors a tenant's search returned 3.9 of 10 rows.

The governing requirement is not maximum approximate-nearest-neighbour throughput. It is that **a query omitting `org_id` must not exist as a normal path**.

| Alternative | Reason it was not selected |
| --- | --- |
| Pinecone / Weaviate Cloud | Additional vendor; isolation becomes a separate metadata filter. |
| Qdrant / Milvus | Strong ANN performance, but a second process to operate and fail independently. |
| Chroma | Suitable for prototypes; not for identity, jobs, and encrypted credentials. |
| FAISS | High speed, but no durable multi-tenant metadata; PostgreSQL would still be required. |
| pgvector | Vectors reside beside the rows that own them. Isolation, search, and jobs share transactions and backups. |

At approximately 400 chunks the planner correctly omits HNSW (exact scan is faster). At approximately 20,000 chunks it uses the index. A feared under-return for a small tenant in a large shared table did not reproduce at that size. It did at 1.1M vectors (3.9 of 10 rows), which is why every connection now sets `hnsw.iterative_scan`.

If a single tenant later reaches millions of chunks, self-hosted Qdrant is the first dedicated index consistent with self-hosting. PostgreSQL would remain the source of truth, and the `org_id` filter would remain mandatory.

---

## 8. Chunking and contextual prefixes

Text is split on paragraphs, packed to **256 tokens** with **40 tokens of overlap** (approximately 15 percent), then hard-split on sentences and words. A final ceiling of **4,000 characters** covers input with no linguistic boundary.

The 256/40 budget is sized so that one policy proposition (an entitlement and its condition) occupies one chunk. Sizes are measured in tokens rather than characters so that dense tables and sparse prose do not produce widely different embedding lengths. Overlap ensures a fact that straddles a heading appears intact in at least one chunk.

Semantic or LLM-based splitters were not used. They add an ingest call per boundary and make chunk identity non-deterministic across re-synchronisations. Policy headings already define the units. The language-model budget is spent on contextual prefixes instead.

Token counts use a **heuristic estimator** by default, not the BGE-M3 neural tokenizer. Counts only determine split points, which are then snapped to word boundaries. Loading `transformers` for exact counts consumed hundreds of megabytes and could exhaust a 512 MB instance. The heuristic slightly underestimates (mean 211 tokens against a 256 budget), which is the safer direction. `CHUNK_TOKEN_BACKEND=hf` remains available on hosts with sufficient memory.

The character ceiling exists because a Google Drive export once contained a 48 KB base64 blob with no whitespace. Linguistic splitters emitted a single chunk, which the embedding API rejected. Base64 is billed at approximately one token per character, so an estimator cannot bound it. A character limit can.

After splitting, a short LLM-generated sentence may be prepended (document, section, and applicable audience). Both the vector and keyword indexes then contain that situating language. The cost is incurred once per chunk at ingest and not at query time, unlike hypothetical-document embeddings (HyDE).

Prefixes are **deferred**: raw chunks are stored first so that chat becomes available; enrichment re-embeds in the background. Documents exceeding 200 chunks skip enrichment. Concurrency defaults to 2, because a burst of 8 exhausts a 15-request-per-minute endpoint and silently drops prefixes.

---

## 9. Retrieval

Plain cosine similarity recovers paraphrases and misses exact terms (for example “part-time” or a form code). Hybrid retrieval surrounds cosine similarity; it does not replace it.

**Stage 1.** Vector search over a pool of 40 candidates (`RETRIEVAL_CANDIDATE_POOL`), in parallel with keyword search. PostgreSQL full-text search bounds the candidate set; Okapi BM25, scored in-process, supplies rank order. PostgreSQL `ts_rank` is not BM25. A BM25 extension such as ParadeDB would require a different PostgreSQL image. Per-tenant corpora are hundreds of chunks, so in-process BM25 is sufficient.

**Stage 2.** Reciprocal Rank Fusion with k = 60. Cosine similarity and BM25 occupy incompatible numeric scales. A weighted sum would require retuning whenever the corpus or model changed. RRF requires no calibration.

**Stage 3.** A cross-encoder reranks the fused pool and retains the top five. BGE-M3 encodes query and chunk separately. The reranker scores the pair jointly.

| Reranker | Assessment |
| --- | --- |
| Cohere or Jina (commercial) | High quality, with added latency, an API key, and data leaving the host. |
| MiniLM | Low latency; weaker on long policy pairs. |
| bge-reranker-v2-m3 | Same family as BGE-M3; already available via `sentence-transformers`. |

The pool was 16 (about 0.3 seconds of local rerank latency) until the Benchmark 2 follow-up raised the default to 40 for recall. A reranked hit scoring below 0.2 of the best hit's reranker score is dropped (`RETRIEVAL_RERANK_MIN_RATIO`). The remote Jina backend (`jina-reranker-v3`) serves hosts that cannot hold the 2.2 GB weights; the hosted and self-hosted deploys use it.

**The confidence gate continues to use cosine 0.35.** RRF scores and reranker logits are never the gate signal. Hybrid search and reranking change only which chunks, and in what order, reach the prompt.

Raising the gate to reject topically related but unanswered questions was evaluated and rejected. On the golden set the lowest answerable score was 0.652. All four unanswerable cases scored between 0.40 and 0.52 and were refused by the prompt. A single threshold cannot make that distinction. The gate remains a noise filter.

Maximal Marginal Relevance was not implemented. Policy answering requires the correct paragraph, not a diverse sample of the handbook.

---

## 10. Query-side mechanisms

These stages sit before or beside retrieval. None of them replace the gate. Generation and the web-search decision use the original, conversation-resolved question. Spelling normalisation is a retrieval key only.

| Mechanism | When it runs | Role |
| --- | --- | --- |
| SymSpell | Every standalone question | Corrects typos in milliseconds at edit distance 1. Capitalised out-of-vocabulary tokens are left unchanged so named entities are not corrupted. |
| Decomposition | Heuristic indicates two distinct asks | A single embedding under-represents the second ask. The heuristic avoids splitting coordinated noun phrases such as “full-time and part-time leave”. |
| Retrieval reuse | Follow-up cosine ≥ 0.72 | Skips hybrid search and reranking for near-verbatim repeats. Reused chunks still pass through the same gate. |
| Bounded recovery | Gate miss or one generation refusal | At most one expansion, re-retrieval, and the same gate. Recovery never answers the question and runs before web search. |

Reuse is conservative. On this corpus a legitimate same-chunk follow-up can score below an adjacent-topic follow-up (approximately 0.63 versus 0.67). An incorrect reuse produces an incorrect refusal; a missed reuse costs only one retrieval.

---

## 11. Grounding, web search, and conversation memory

**Layer 1 — confidence gate.** If the best cosine similarity is below 0.35, the answer model is not called (recovery may run once). Unrelated noise, typically around 0.30, is rejected cheaply.

**Layer 2 — grounded prompt.** Three modes only:

- **Explicitly supported.** Answer from the retrieved chunks and cite them.
- **Related but not explicit.** Report what the documents state and that they do not answer the question. Unsupported conclusions are forbidden.
- **No supporting evidence.** Emit the single fallback string shared by the gate, the prompt, and refusal detection.

**Web search** is not a general search escape. The model is offered a tool only after internal evidence has failed, and only for a named external entity (for example an insurer or a public product). Internal company questions remain on the fallback. The default provider is DuckDuckGo (`ddgs`). Tavily is the documented production alternative. Web answers carry a visible banner and `source=web`. Exactly one tool round is permitted.

**Conversation memory.** Follow-ups such as “what about part-timers?” are rewritten using the last three turns and a running summary, then processed by the unchanged retrieve–gate–generate path. Summaries fold one turn at a time (`existing summary + that turn`) off the critical path. State is stored in PostgreSQL. A conversation belongs to one person (`conversations.user_id`).

There is **no answer cache** (removed 6 Nov 2026). A cache keyed on the scope would serve one person's answer to the next asker, whose access may differ.

---

## 12. Sources and agents

Ingestible sources implement `SourceAdapter`. Format conversion occurs inside the adapter. The ingestion pipeline does not change when a source is added.

| Source | Integration | Rationale |
| --- | --- | --- |
| Notion | Official SDK; per-organisation OAuth | The integration sees only pages shared with it, which is an external tenant boundary. |
| Google Drive | httpx and native Docs export | OAuth only. There is no environment-token path that could be confused with Notion. |
| Slack | OAuth; channels picked before sync. The Slack bot also answers questions | A public channel is open to the company; a private channel's members are its ACL. |
| Linear | OAuth; teams picked in Handbook | A team's membership is its issues' ACL. |
| GitHub | Live tools; not ingested | Source code is a poor embedding target. README indexing would introduce staleness. `resolve_repo` is the analogue of `org_id` for model-supplied repository names. |

Google and Slack (sync, sign-in and Agent mode) use each company's **own** app (`app/auth/org_apps.py`), else the one in `.env`. Hosted Handbook leaves `.env`'s unset, so there the company's own app is required.

There is one agent per source (`app/agent/`). The retrieval agents are thin adapters over `RagPipeline`; `GitHubAgent` implements `Agent` directly. Routing (`app/agent/routing.py`) is a measurement, not an LLM classifier: one embedding of the question against each provider's content picks the source. A tool the question names decides; GitHub is reached when the question names an authorised repository or shows code intent.

A workspace without a GitHub connection must not fall back to the organisation installation. That fallback would convert a workspace invitation into unintended repository access.

---

## 13. Product layer and evaluation

**HTTP API.** FastAPI. Organisation identity is taken only from the signed session cookie. Chat streaming delivers an already-decided answer in chunks. Streaming raw tokens from a generation that may still be discarded (recovery, then web search) would leak a draft.

**Authentication.** Magic-link sign-in (delivered through SendGrid in production), Google and Slack sign-in through the company's own app, and administrator-invited members. Invites and email changes must match the company's email domain (`organizations.email_domain`). Creation of a new organisation is gated by a human-reviewed email queue: GET renders a confirmation page; POST performs the action, so mail scanners cannot approve a request. Tokens at rest are encrypted with MultiFernet; there is no external KMS.

**Ingestion jobs.** PostgreSQL `FOR UPDATE SKIP LOCKED`, not Redis or Celery. Stuck jobs are reaped by silence (`progress_at`), not by elapsed start time. A job that repeatedly terminates the process is abandoned after a bounded number of attempts rather than restarting the entire service.

**Rate limits.** Token buckets (`app/ratelimit/`), in Redis when `REDIS_URL` is set (production runs Valkey with `volatile-lru`), else per process. Redis holds rate limits only, and every key has a TTL. A question takes the person's and the company's bucket in one step. AI usage has two more layers (`app/security/ai_spend.py`): tokens a minute per answering model, input and output apart, per person and per company (`AI_TOKEN_LIMITS`); and dollars a day, one wallet whichever model answers (`AI_COMPANY_DOLLARS_PER_DAY`, `AI_PERSON_DOLLARS_PER_DAY`, priced by `AI_PRICES`). Each paid call is charged where it is made. A limiter fault lets the request through.

**Frontend.** Next.js 15 with application CSS. The browser calls same-origin `/api`, rewritten to FastAPI, so `SameSite=Lax` cookies remain first-party on a split Vercel and Render deployment. Directing the browser at the API host is the configuration that makes a successful login appear as an immediate logout.

**Evaluation is split by cost.**

| Tier | What it establishes | Cadence |
| --- | --- | --- |
| Retrieval rank | The correct chunk appears in the top band, without an LLM. | Fast. |
| Golden path set | 29 cases (`evaluation/golden_set.py`) covering policy, fallback, web, and conversation. | Every push. |
| RAGAS | Faithfulness and related scores against a baseline. | Nightly, using this system’s LLM and local BGE-M3. |

A small, hand-selected set that fails loudly is preferred to a large, noisy synthetic set. On the golden set the 0.35 gate produced no false negatives (lowest answerable score 0.652). Unanswerable cases were refused by the prompt. The threshold should not be raised into a gap observed on seventeen cases (the set's size when this was measured).

---

## 14. Alternatives not adopted

| Alternative | Reason it is not used | When to reconsider |
| --- | --- | --- |
| Fine-tuning on policies | Facts become stale; citations are lost. | Not for entitlements. |
| LangChain as the application spine | The framework would own too much of the path. | Unlikely. |
| LiteLLM | Native features are unused. | Prompt caching on a commercial host. |
| Pinecone | Additional vendor; isolation is split across systems. | Extreme scale with managed ANN. |
| Redis / Celery for jobs or answers | Jobs use `SKIP LOCKED`; an answer cache would cross people's access. Redis holds rate limits only. | Ingestion volume that PostgreSQL cannot absorb. |
| Maximal Marginal Relevance | Incorrect objective for policy answering. | Survey-style handbook queries. |
| Always-on LLM query rewrite | Permanent latency on every request. | Production logs show SymSpell and recovery are insufficient. |
| Embedding GitHub | Code is not prose; indexed READMEs go stale. | Fuzzy search across many repositories. |
| LLM agent router | Non-determinism in front of the tenant path. The embedding probe measures instead. | The probe stops separating sources. |

---

## 15. Combined effect

Each layer addresses a failure the previous layer cannot:

- **Chunking** makes the retrieval unit a policy proposition rather than a page.
- **Contextual prefixes** place that proposition in a document for both indexes.
- **BGE-M3** recovers paraphrases; **BM25 and RRF** recover exact terms; the **cross-encoder** reorders the shortlist.
- **SymSpell, decomposition, recovery, and reuse** improve wording and cost without altering grounding.
- **Gate and prompt** prevent fluent unsupported answers; **labelled web search** covers questions that internal policy will never contain.
- **pgvector in the application database** keeps isolation, jobs, memory, and vectors in one operational unit.

Current defaults: chunk size 256 with overlap 40; gate 0.35; reuse 0.72; candidate pool 40; RRF k = 60; top five, up to ten on a wide read; BGE-M3 and bge-reranker-v2-m3 locally, Jina remotely; PostgreSQL with pgvector (`halfvec(1024)`, one `chunks` partition per organisation). If implementation and this document diverge, the implementation is authoritative.
