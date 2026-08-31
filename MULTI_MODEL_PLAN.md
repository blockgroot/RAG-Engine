# Multi-Model Selection — design & phased plan

Branch: `feat/multi-model-selection`

Let a member choose which model answers, on every surface where they write a
prompt. Default is unchanged: the Gemini config already in `LLM_MODEL` /
`LLM_BASE_URL`. Alternatives are served through **one OpenRouter key**.

## 1. Understanding

- **What** — a model dropdown on every prompt surface (chat composer,
  scheduler setup chat, scheduler reports), default `auto`.
- **Why** — `LLMProvider` was built for this swap (§3) but only exercises it at
  deploy time. Model choice is user-visible capability + cost control.
- **Who** — every member. Not admin-only.
- **Non-goals** — BYO per-org keys; spend caps; per-model metering dashboards;
  an LLM router that picks a model by question difficulty (§3: routing is
  deterministic, no LLM picks the agent).

## 2. Constraints that shaped the design

- **Agents are `lru_cache(maxsize=1)` singletons** holding embedder +
  cross-encoder weights (`app/api/deps.py`). Keying that cache by model would
  load a second copy of BGE-M3 per choice — the §5 tokenizer incident again.
  **⇒ the model must be a per-call value, never a constructor arg.**
- **Auto and a selected model use different endpoints**: Auto is Gemini
  (`LLM_BASE_URL` + `LLM_API_KEY`); a selection is OpenRouter (its own base_url
  + key). So this is not a model-string swap — it is a second client.
- **Tenant content is in every prompt.** `provider.data_collection = "deny"` is
  sent on every OpenRouter call so a training provider is never routed to. Not
  optional, not an account-level toggle.
- **Never switchable**: embeddings (`EMBEDDING_DIM` is coupled to
  `chunks.embedding`, §6), the reranker, and **ingest contextualization** —
  per-user models there would write inconsistent chunks into one index.
  Enforced structurally: only `build_llm_provider` is routed;
  `build_aux_llm_provider` (used by `app/ingestion/`) is not.

## 3. Design

**`RoutedLLMProvider`** (`app/llm/routed.py`) — a thin `LLMProvider` that holds
the configured default and dispatches per call on a `ContextVar`:

- unset  → the existing default provider, byte-identical to today's behaviour.
- set    → a lazily built, cached `OpenAICompatProvider` pointed at OpenRouter.

`build_llm_provider()` returns this wrapper; `build_aux_llm_provider()` does
not. That one line is what makes ingestion structurally unroutable.

It proxies `last_usage` and `model` to the *active* provider, so
`metering.log_llm_call` keeps working and logs the model that actually ran.

**Why a wrapper, not a flag on `OpenAICompatProvider`** — the override changes
endpoint *and* key, not just the model string. And nothing downstream changes:
`RagPipeline`, every agent, and `schedulers/runner.py` keep their single
injected `llm`.

**Why a ContextVar, not a threaded parameter** — the pipeline calls the LLM
from ~6 places (generate, recovery, tone retry, audit, decomposition, fold).
Threading a `model=` through all of them is a wide diff for a value that is
genuinely request-scoped. Set once at the request boundary, read at the call.
`# ponytail:` ceiling — if two *different* models are ever needed in one
request beyond main/aux, thread it explicitly.

**Catalog** (`app/llm/catalog.py`) — ~5 hardcoded `ModelChoice` entries. Not
fetched from `/models`: 5 verified ids beat 300 unverified ones, and a live
fetch adds a network dependency to page load. Free models rotate out, so the
catalog is revalidated by the phase-0 script, not at runtime.

**OpenRouter request extras** — `extra_body` on every routed call:

```json
{"provider": {"data_collection": "deny", "require_parameters": true},
 "reasoning": {"exclude": true}}
```

- `data_collection: deny` — tenant content never reaches a training provider.
- `require_parameters: true` — server-side capability guard. Send `tools` and
  OpenRouter will not route to a provider that cannot do function calling, so
  `GitHubAgent` (structural grounding, no tool call ⇒ fixed fallback) cannot
  silently return the fallback for every question.
- `reasoning.exclude` — keeps `<think>` blocks out of `content`; see §4.

## 4. The real risk: the prompt contract, not the wire format

OpenRouter normalizes to the OpenAI Chat Completions schema, so
`OpenAICompatProvider` needs no format changes. What breaks is *our* contract:

`_MODE_TAG_RE` (`app/rag/pipeline.py:92`) is anchored at the start of the
response. A model that emits a `<think>` block or a "Sure, here's…" preamble
produces no match ⇒ `mode = None` ⇒ **the groundedness audit at
`pipeline.py:923` (`mode in ("A","B")`) never runs** and the tone retry cannot
fire. The answer still renders, so the lost validation layer is invisible.

Mitigations, cheapest first: `reasoning: {exclude: true}`; tolerate a leading
`<think>…</think>` in `_MODE_TAG_RE`; and — the real one — **the golden set is
the admission test**: a model does not enter the catalog until it parses mode
tags, emits the fallback string verbatim, and makes tool calls.

Related: `generate()` raises on empty `content`, which is what a reasoning
model returns if everything lands in `reasoning`. Covered by the same switch.

## 5. Correctness fixes this feature requires

1. **`query_answer_cache` must key on the model** (`app/rag/query_cache.py`) —
   otherwise a Gemini answer is served to someone who asked on Claude. Folded
   into `_question_hash` the same way `workspace_id` / `source_provider` /
   `date_range` already are, appended only when set so every existing key is
   unchanged.
2. **Validate before the stream opens** — an unknown model is a 400 in
   `chat_stream`, never inside the generator: a `StreamingResponse` cannot set
   a status code once headers are sent.
3. **Set the ContextVar inside `_stream_answer`**, not in `chat_stream` —
   Starlette runs the sync generator via `iterate_in_threadpool`, so a context
   set before returning the response is not reliably the generator's own.

## 6. Phases

- **0 — verify** `scripts/verify_openrouter_models.py`: per candidate, assert
  non-empty content, MODE tag parses, a tool call round-trips, and record the
  resolved `response.model`. Gates entry into the catalog.
- **1 — backend** catalog, `RoutedLLMProvider`, `OpenRouterSettings`,
  cache-key fix, `GET /chat/models`, `model` on `POST /chat/stream`,
  resolved model on the `done` event.
- **2 — chat UI** native `<select>` in the composer, sticky via
  `localStorage`, "answered by X" on the turn.
- **3 — schedulers** `model` column, picker on the setup page, snapshot onto
  `scheduler_reports` (same rule as prompt/provider/space_name).

## 7. Decision log

| Decision | Alternatives | Why |
|---|---|---|
| OpenRouter as the multi-model backend | LiteLLM; per-vendor SDKs | §3 already bet on an OpenAI-compatible client. One key, one wire format, no new dependency. |
| Auto = existing Gemini default | `openrouter/free` (random free model) | Deterministic; today's behaviour unchanged when nobody touches the dropdown; random quality per run is not a feature. Matches §3 "no LLM picks the agent". |
| ~5 hardcoded models | Live `/models` fetch | Free models rotate out, so a fetched list is unverified. 5 golden-set-admitted ids beat 300 unknown ones. |
| `RoutedLLMProvider` wrapper | Flag on `OpenAICompatProvider`; per-model agent cache | The override changes endpoint + key, not just a model string; the agent cache holds model weights and must stay `maxsize=1`. |
| ContextVar | Threaded `model=` parameter | ~6 call sites inside the pipeline; the value is request-scoped by nature. |
| `data_collection: deny` per request | Account-level privacy toggle | Per-request, enforced server-side, and never applies a training policy to all tenants at once. |
| Free tier, Auto stays on Gemini | $10 once for 1,000 req/day | OpenRouter is only hit on an *explicit* selection, so the 50/day cap covers opt-in traffic, not baseline load. Revisit if selections become the norm. |
