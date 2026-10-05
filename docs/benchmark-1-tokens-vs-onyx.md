# Benchmark 1: Token use and context management, Handbook vs Onyx

As of 2026-10-05. This applies Benchmark 1 from `benchmarks (1).md` and follows its seven rules for running a benchmark. Every claim about Onyx or the dataset below was checked against their source code on this date (Onyx `main` @ `b017b8b`, EnterpriseRAG-Bench `main`). Anything not yet checked is marked **Unverified**.

## 1. What we measure

**Main number: LLM tokens per correct answer.** That is the total LLM tokens used across all runs, divided by the number of answers graded `correct`.

Reported beside it, for every system, so no number can improve by quietly making another worse:

| Number | Definition |
| --- | --- |
| Tokens per question | Input and output LLM tokens per question, split into the **answer path** (everything needed to produce the answer) and **all** (answer path plus extras such as tone, chat naming and follow-up suggestions) |
| LLM calls per question | Number of model requests seen by the proxy |
| Tokens per step | Per-question tokens for each step (rewrite, generate, tone, Onyx's search decision, …) |
| Context sent | Input tokens of the answer-generation call, plus the number of documents in it |
| Context precision | Gold documents sent ÷ documents sent. Judge-free, using the benchmark's gold document ids |
| Document recall | Gold documents sent ÷ gold documents (from the grader) |
| Correctness, completeness | From the EnterpriseRAG-Bench grader |
| Made-up rate, wrong "I don't know" rate | 5-bucket mapping (section 7). Rule 7: always shown next to the wins |
| Embedding and rerank tokens | Jina tokens. Indexing is reported once, query-time cost per question |
| Cost per question | Tokens × the published paid price of the model, labelled "if paid". The runs themselves cost $0 on free tiers |

Questions of type `completeness` and `high_level` are reported in their own table, because benchmarks.md says "summarise everything" style questions must be tracked separately.

## 2. Systems compared

| System | How it runs | Notes |
| --- | --- | --- |
| **Handbook, core mode** | `RagPipeline.answer` with no tool pinned, single turn | The number to compare. Product mode (router) comes later |
| **Onyx** (open-source, self-hosted) | `POST /chat/send-chat-message` with `stream=false`, `deep_research=false`, default agent | Leave its tool choice as the product ships it, and record whether search was called (`tool_calls`) |
| **Vector baseline** | EnterpriseRAG-Bench `vector_retrieval.py`, top 10 | Needs a 1-line patch: the embedding model is hard-coded to `text-embedding-3-large` |
| **BM25 baseline** | EnterpriseRAG-Bench `bm25_retrieval.py`, top 10 | Needs an OpenSearch container. Run it only while Onyx is stopped |
| **Paste everything** | Gold docs plus fixed noise documents, up to the model's context limit, one call | A reduced version: 20k docs cannot fit in one prompt. Say so in the report |

## 3. What stays fixed (rule 1 and rule 2)

| Setting | Value | Applies to |
| --- | --- | --- |
| Answer model | `openai/gpt-oss-120b`, **one host for every system** (section 4) | All |
| Reasoning effort | Pinned in the proxy config (same value for every request) | All |
| Embedder | Jina `jina-embeddings-v3` (1024 dims, matching `EMBEDDING_DIM`) | Handbook, Onyx, vector baseline |
| Reranker | Jina `jina-reranker-v3`, **on for both** Handbook and Onyx | Handbook, Onyx |
| Data | Same pilot manifest (section 5) | All |
| Questions | Same 50, same dev/test split | All |
| Runs | 3 per question on test, 1 on dev | All |
| Our switches | `QUERY_CACHE_ENABLED=false`, `GUARD_MODE=off`, contextual chunk prefix off at ingest, web search off, live tools off, graph off, personal memory off, `RAG_AUDIT_ENABLED` as in production | Handbook |
| Onyx switches | `deep_research=false`, multi-model off, any extra mini-chunk embedding off if present, version and image tag recorded | Onyx |

Every result file carries: git commit, Onyx commit and image tag, model and host, reasoning effort, embedder, reranker, top_k, all switches above, date, and the pilot manifest hash.

**Why Jina for both:** both systems use the same embedder and reranker, so any token difference comes from the pipeline, not the embedding model. The report states "same embedder and reranker for both (Jina), not Onyx's defaults".

## 4. Accounts and limits (all free)

| Need | Choice | Limit to plan around | Check |
| --- | --- | --- | --- |
| Answer model | **Groq**, `openai/gpt-oss-120b` | 30 RPM, **8,000 TPM**, 1,000 requests/day, per organization | Confirm on your Groq limits page and record it |
| Answer model fallback | **Cerebras**, `gpt-oss-120b` | About 5 RPM, 30k TPM, 1M tokens/day (third-party figures) | Use only if Groq's TPM cap rejects Onyx requests (below) |
| Embeddings and rerank | **Jina**, one key | One-time 10M free tokens, shared by embedding and rerank | Read the balance before indexing |
| Judge 1 | **Gemini Flash**, free tier (Google) | Free daily cap | Different family from the answer model |
| Judge 2 | **Groq** `llama-3.3-70b-versatile` (Meta) | 1,000 requests/day, 12k TPM. Separate quota from gpt-oss | Different family from the answer model |

**Two risks to check first:**

1. **Groq's 8,000 TPM can reject one large request.** Groq refuses a request bigger than the per-minute cap. Our answer call is about 4k tokens. Onyx's is unknown and may be larger, since it fills the context with many documents and tool definitions. Measure Onyx's largest request in Phase 1. If any request is over about 7k tokens, move **every** system to Cerebras. Don't shrink Onyx's context to fit: that changes the product being measured.
2. **Jina's 10M one-time tokens.** Measure the pilot's token count before indexing (section 5). If it doesn't fit for both systems plus queries, reduce the noise documents, the same for every system.

Do not create extra keys to get around a free limit. If one runs out, wait for the reset or reduce the noise documents.

## 5. Dataset: EnterpriseRAG-Bench pilot

Facts checked in the repository:

- `questions.jsonl`: 500 lines, fields `question_id, question_type, source_types, question, expected_doc_ids, gold_answer, answer_facts`.
- Ten question types. `high_level` (10) and `info_not_found` (20) have **no** gold documents.
- Documents: `all_documents.zip` (about 1.26 GB) from the GitHub release or Hugging Face. The repository copy stores each document as JSON with `title_field_name` and `content_field_names`, and `src/utils/document_content.py::extract_document_content` reads it. **Unverified:** whether the release zip is the same JSON or plain `.txt`. Inspect it and reuse `extract_document_content` if it's JSON.

Build steps (`evaluation/erb/build_pilot.py`):

1. Sample 50 questions with seed `20261005`, stratified by `question_type` so all ten types appear (5 each), including all three types with no gold documents.
2. Take every gold document of those questions, plus N noise documents, sampled with the same seed from the rest. Start with N = 20,000.
3. **Measure:** total characters ÷ 4 ≈ tokens. Indexing cost ≈ tokens × 2 systems + tokens × 1 (vector baseline). Compare against the Jina balance and reduce N if needed.
4. Split dev/test 25/25, stratified by type. Write `manifest.json` (question ids, split, document ids, seed, N, sha256), and never edit it afterwards.

## 6. Shared setup

### 6.1 Token proxy (the one place tokens are counted)

Run a LiteLLM proxy on the Mac, port 4000. Every system's **chat** calls go through it. Embedding and rerank go **directly** to Jina from both systems, because Onyx's LiteLLM provider posts exactly Jina's format (checked below), and their tokens are read from the Jina dashboard before and after each stage.

- `config.yaml`: one model entry named `bench-answer` pointing at Groq (or Cerebras) `gpt-oss-120b`, with the pinned reasoning effort in `litellm_params`. **Unverified:** that a client-sent `reasoning_effort` is overridden by the config value. Check it with one request.
- A custom logger callback appends one JSON line per request to `evaluation/reports/bench1/proxy.jsonl`: `ts_start, ts_end, model, input_tokens, output_tokens, reasoning_tokens (if reported), stream, status, system_prompt_sha (first 200 chars), first_user_chars (first 80)`. The callback receives usage for streamed calls too, which a hand-written pass-through would have to parse from the stream.
- **Per-question attribution:** the runner sends one question at a time, writes `q_start`/`q_end` markers, and waits 5 s after each answer before starting the next. A question's calls are the proxy lines inside its window. Lines arriving in the wait are tagged `background`.
- **Step labels:** from `system_prompt_sha`, through a hand-written map built in Phase 1 (`stage_map.json`). Cross-check our labels against `metering.py`'s stage names on 5 golden-set questions. Totals must match on calls that don't run in parallel.

### 6.2 Handbook

- Local Postgres plus pgvector (CLAUDE.md §5: `brew install pgvector`, `createdb bench1`, `apply_schema()`), never Supabase.
- `.env.bench`: `LLM_ADAPTER=custom` with its base URL set to the proxy and `LLM_MODEL=bench-answer`; `EMBEDDING_BACKEND=remote` with the Jina model and key; `RERANKER_BACKEND=remote`; plus the switches from section 3.
- `list_chunk_texts` (the spelling-fix vocabulary in `app/vectorstore/pgvector_store.py`) loads every chunk of the org into memory. Cap or disable it for the run, as benchmarks.md requires.

### 6.3 Onyx (Docker, Standard, never Lite)

Lite turns off the search index and connectors. Checked in `deployment/docker_compose/`:

- Services: `api_server, background, web_server, inference_model_server, indexing_model_server, relational_db, opensearch, nginx, cache, minio`. **The search index is now OpenSearch**, not Vespa.
- `DISABLE_MODEL_SERVER` exists in `env.template`. With cloud embedding and rerank, try `true` to save RAM. **Unverified:** that search still works with it on. If it doesn't, leave it off and give Docker about 12 GB.
- Run only while our stack is stopped (16 GB Mac). Containers reach the proxy at `http://host.docker.internal:4000`.

Configuration in the Onyx admin UI:

- **LLM:** an OpenAI-compatible custom provider, base `http://host.docker.internal:4000`, model `bench-answer`, set as the default.
- **Embedding:** provider `litellm`, `api_url = https://api.jina.ai/v1/embeddings`, model `jina-embeddings-v3`, the Jina key. Checked: `search_nlp_models.py::_embed_litellm_proxy` posts `{model, input}` to `api_url` and reads `data[].embedding`, which is Jina's format.
- **Reranker:** provider `litellm`, `api_url = https://api.jina.ai/v1/rerank`, model `jina-reranker-v3`. Checked: `litellm_rerank` posts `{model, query, documents}` and reads `results[].relevance_score`, which is Jina's format.
- **Unverified:** whether the admin UI accepts a non-LiteLLM URL in these fields without a validation call that fails. If it does reject it, put a LiteLLM proxy route for Jina in front of it.

Loading documents. Checked in `server/onyx_api/ingestion.py`: `POST /onyx-api/ingestion` takes `{"document": DocumentBase, "cc_pair_id"}` and needs an admin API key. Set `DocumentBase.id = dsid_…`, so `top_documents[].document_id` comes back as the dsid with no mapping table.

Asking questions. Checked in `server/query_and_chat/chat_backend.py` and `chat/models.py`: `POST /chat/send-chat-message` with `{"message", "stream": false, "deep_research": false, "chat_session_info": {...}}` returns `ChatFullResponse` with `answer`, `answer_citationless`, `tool_calls`, `top_documents`, `citation_info`, `error_msg`. Use a new chat session per question and run, so no history leaks between runs.

## 7. Grading

Use EnterpriseRAG-Bench's `metrics_based_eval`, with two required flags:

- `--no-correction`. Without it the grader rewrites gold answers, facts and document sets based on the submitted answers, so each system would be graded against a different answer key.
- `--skip-citation-stripping`. Submit `answer_citationless` for Onyx and our plain answer, which also saves one judge call per answer.

**The grader needs a small adapter.** Checked in `src/llm/openai_llm.py`: it calls OpenAI's **Responses API** (`client.responses.create`, streaming, with `reasoning`). The `openai` SDK honours `OPENAI_BASE_URL`, but Groq's Responses API serves only the gpt-oss models (the answer model's family, so it can't judge itself), and Gemini's OpenAI-compatible endpoint isn't known to support Responses (**Unverified**). Add a `ChatCompletionsLLM` class (about 40 lines) implementing their `LLM` interface (`src/llm/interface.py`) on `chat.completions`, selected by `LLM_PROVIDER=chat_compat`, and run the grader twice:

| Run | `OPENAI_BASE_URL` | Model |
| --- | --- | --- |
| Judge 1 (Google) | `https://generativelanguage.googleapis.com/v1beta/openai/` | Gemini Flash |
| Judge 2 (Meta) | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |

Put the patch in our repository (`evaluation/erb/grader_patch/`) and pin their commit, so anyone can reproduce the grading.

The grader auto-resumes, so a daily cap only pauses it.

**Hand check (rule 5):** a person grades a random 10% of the answers per system without seeing the judges' grades. Report agreement between the two judges and between each judge and the person. If the judges disagree on more than about 15%, fix the grading rules and re-grade. Never adjust the numbers.

**5 buckets from the grader's output:**

| Bucket | Rule |
| --- | --- |
| Correct | Judged correct, all facts present |
| Partial | Judged correct, some facts missing |
| Made up | Judged incorrect, and not a refusal |
| Honest "I don't know" | Refusal on an `info_not_found` question |
| Wrong "I don't know" | Refusal on any other question |

Refusal detection: ours = the fixed fallback text (`grounded is False`). Onyx = a fixed list of refusal phrases built from the dev answers, then frozen, and spot-checked in the hand check.

## 8. Runners

Shared output for each system, run and question, in `evaluation/reports/bench1/<system>/run<k>/`:

- `answers.jsonl`: `{"question_id", "answer", "document_ids"}`, the grader's format. `document_ids` = the **documents in the answer prompt**. Ours: `RagResult` hits → `RetrievedChunk.source_external_id`. Onyx: `top_documents[].document_id`.
- `records.jsonl`: the above plus `q_start, q_end, total_seconds, retries, cache_hit, tool_calls, error, raw response`.

Runner rules:

- One question at a time, then the 5 s wait.
- On HTTP 429: sleep for `Retry-After` and retry the same question. The wait goes in `retries` and stays out of `total_seconds`.
- Resume from the last finished question. Never restart a run.

Files to write:

| File | Purpose |
| --- | --- |
| `evaluation/erb/build_pilot.py` | Section 5 |
| `evaluation/erb/load_handbook.py` | Writes documents and chunks into local Postgres, `source_external_id = dsid`, `external_workspace_id` set to a fixed fake value (NOT NULL). Provider map: slack→`slack`, linear→`linear`, google_drive→`google`, github→`github_docs`, everything else→`erb_other` |
| `evaluation/erb/load_onyx.py` | Section 6.3 ingestion, then waits until Onyx reports the same document count |
| `evaluation/erb/run_handbook.py`, `run_onyx.py` | This section |
| `evaluation/erb/join_tokens.py` | Proxy log + `records.jsonl` → per-question token table, using `stage_map.json` |
| `evaluation/erb/report.py` | Section 10 |

## 9. Context-management experiments (Handbook only, dev split, 1 run)

1. **Top_k sweep:** `RAG_TOP_K` = 3, 5, 10, 20, with `RETRIEVAL_CANDIDATE_POOL` ≥ top_k. Plot answer-path tokens against correctness, and report the smallest top_k after which correctness stops rising.
2. **Breadth questions:** tokens for `completeness` and `high_level` questions, shown separately.
3. **Our extras:** answer-path tokens vs all tokens, to show what tone, recovery and decomposition cost.

## 10. The report

`evaluation/reports/bench1/REPORT.md`:

1. Status line: "Pilot: 50 questions, N noise documents, free-tier judges. Not the full benchmark."
2. Settings block (section 3).
3. Main table. Rows: Handbook, Onyx, vector, BM25, paste-everything. Columns: tokens per correct answer, tokens per question (answer path / all), LLM calls, context sent, context precision, recall, correctness, completeness, made-up %, wrong "I don't know" %. Every value is the test-split mean ± min–max over 3 runs.
4. Per-step token breakdown for each system.
5. Breadth questions table.
6. Top_k curve (section 9).
7. Judge agreement table (section 7).
8. What this doesn't show: pilot size, one model, uploaded files not live connectors, Jina instead of Onyx's default embedder, Onyx's normal chat mode only.
9. Links to every raw file.

## 11. Order of work and the check that ends each phase

| # | Phase | Done when |
| --- | --- | --- |
| 1 | Accounts, proxy, Onyx installed, 10 questions asked by hand | Proxy log shows both systems' calls. Onyx's largest request is recorded (risk 1, section 4). `stage_map.json` written. Docker memory recorded |
| 2 | Pilot built and measured | `manifest.json` frozen. Jina budget fits |
| 3 | Both loaders, tried on 100 docs first, then the full pilot | Both systems report the same document count. Jina tokens recorded |
| 4 | Runners, 5 dev questions on both systems | `answers.jsonl` passes the grader with `--limit 5`. Proxy totals agree with `metering.py` |
| 5 | Grader patch, 2 judges | Both judges grade the 5 questions |
| 6 | Full runs: test ×3 for all systems, dev ×1 | All records complete, no gaps in the proxy log |
| 7 | Grading, hand check, top_k sweep | Agreement table filled in |
| 8 | Report | Every number traceable to a raw file |

## 12. Sources

- EnterpriseRAG-Bench: [repository](https://github.com/onyx-dot-app/EnterpriseRAG-Bench), [quickstart](https://raw.githubusercontent.com/onyx-dot-app/EnterpriseRAG-Bench/main/quickstart.md), [paper](https://arxiv.org/abs/2605.05253)
- Onyx source: [repository](https://github.com/onyx-dot-app/onyx). Files read: `backend/onyx/server/onyx_api/ingestion.py`, `backend/onyx/server/query_and_chat/{chat_backend,models}.py`, `backend/onyx/chat/models.py`, `backend/onyx/natural_language_processing/search_nlp_models.py`, `backend/shared_configs/enums.py`, `deployment/docker_compose/{docker-compose.yml,env.template}`
- Onyx: [deployment overview](https://docs.onyx.app/deployment/overview), [resourcing](https://docs.onyx.app/deployment/getting_started/resourcing) (Standard: 10 GB minimum, 16 GB+ preferred)
- Groq: [Responses API](https://console.groq.com/docs/responses-api), [free-tier limits (BenchLM)](https://benchlm.ai/free-tier/groq), [eesel](https://eesel.ai/blog/groq-pricing)
- Cerebras free tier: [fast.io](https://fast.io/resources/cerebras-rate-limit/), [BenchLM](https://benchlm.ai/md/free-tier/cerebras.md)
- Jina pricing: [markaicode](https://markaicode.com/pricing/jina-ai-pricing/), [yangmao](https://yangmao.ai/en/providers/jina-ai/free-tier/)
