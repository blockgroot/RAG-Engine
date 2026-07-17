# RAG Engine

A multi-tenant RAG policy Q&A platform, built in small, reviewable phases.

> **Current phase:** LLM & Embedding provider abstraction layer only.
> The RAG pipeline, database schema, agents, and orchestrator are future phases
> and are intentionally **not** built yet.

## Provider abstraction layer

This gives the rest of the application one small, stable interface for LLM and
embedding calls, so it never touches provider-specific details directly. The app
depends on **interfaces** (`LLMProvider`, `EmbeddingProvider`); concrete
implementations are chosen by **factories** from config. Swapping an
implementation later requires no changes downstream.

The LLM side is **provider-agnostic** via the official `openai` client + a
`base_url` swap: OpenAI, Google Gemini, Anthropic/Claude, a self-hosted
OpenAI-compatible endpoint (like FreeLLMAPI), or a local model are all reachable
through the same `generate()` call. **Switching provider is a config change
(`LLM_MODEL` + `LLM_API_KEY` + `LLM_BASE_URL`) — no code changes.**

### Layout

```
app/
  config/
    settings.py          # one place that reads env -> typed settings
  core/
    exceptions.py        # shared ProviderError hierarchy
  llm/
    base.py              # LLMProvider interface (contract)
    openai_provider.py   # OpenAICompatProvider (openai client)
    factory.py           # build_llm_provider() -> LLMProvider
  embeddings/
    base.py              # EmbeddingProvider interface
    local.py             # LocalEmbeddingProvider (sentence-transformers)
    remote.py            # RemoteEmbeddingProvider (HTTP OpenAI-compatible)
    factory.py           # build_embedding_provider() -> EmbeddingProvider
scripts/
  verify_providers.py    # manual smoke test for both providers
.env.example             # expected environment variables

# future phases get their own top-level packages: app/rag/, app/db/, app/api/, ...
```

### Public API

```python
from app.llm import build_llm_provider
from app.embeddings import build_embedding_provider

llm = build_llm_provider()               # from env
print(llm.generate("Hello"))

embedder = build_embedding_provider()    # from env (local or remote)
vectors = embedder.embed(["some text"])  # list[list[float]]
```

- **`LLMProvider`** (interface) — `generate(prompt: str) -> str`. Implemented by
  `OpenAICompatProvider`.
- **`EmbeddingProvider`** (interface) — `embed(texts: list[str]) -> list[list[float]]`.
  Two implementations, interchangeable:
  - **`LocalEmbeddingProvider`** — runs BGE-M3 **in-process** via
    `sentence-transformers`: $0, no key, text never leaves the machine (default).
  - **`RemoteEmbeddingProvider`** — a remote OpenAI-compatible embeddings
    endpoint (e.g. DeepInfra) over HTTP.

All implementations convert underlying errors (timeouts, connection failures,
model load failures, API errors) into a clear custom `ProviderError`, so callers
only need to catch one exception type.

### Why the `openai` client and not a multi-provider library?

The `openai` client speaks the OpenAI wire format, which OpenAI, Gemini, and
Anthropic all expose an endpoint for — so a `base_url` swap is enough for basic
chat, with the fewest dependencies. The Gemini and Anthropic OpenAI-compatible
endpoints are vendor-labelled **beta/testing** (no prompt caching, some params
dropped). If we later need those native features or built-in fallbacks, the
`LLMProvider` interface + factory let us drop in a LiteLLM-backed implementation
without touching the rest of the app.

### Configuration

Copy the example env file and fill in real values:

```bash
cp .env.example .env
```

| Variable            | Purpose                                        | Example                    |
| ------------------- | ---------------------------------------------- | -------------------------- |
| `LLM_MODEL`         | Model id                                        | `auto`, `gpt-5`, `claude-sonnet-5` |
| `LLM_API_KEY`       | API key for the chosen provider                | `freellmapi-…`             |
| `LLM_BASE_URL`      | Endpoint; empty = OpenAI default                | `http://localhost:3001/v1` |
| `EMBEDDING_BACKEND` | `local` (default) or `remote`                   | `local`                    |
| `EMBEDDING_MODEL`   | Embedding model                                 | `BAAI/bge-m3`              |
| `EMBEDDING_DEVICE`  | Local only; optional device override            | `cpu` / `cuda` / `mps`     |
| `EMBEDDING_API_KEY` / `EMBEDDING_BASE_URL` | Remote backend only         | DeepInfra key / URL        |

**Switching LLM provider** — set these three vars (see `.env.example` for
copy-paste blocks):

| Goal                     | `LLM_MODEL`         | `LLM_BASE_URL`                                            |
| ------------------------ | ------------------- | -------------------------------------------------------- |
| FreeLLMAPI / self-hosted | `auto`              | `http://localhost:3001/v1`                               |
| Native OpenAI            | `gpt-5`             | *(empty — default)*                                      |
| Google Gemini            | `gemini-3.5-flash`  | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| Anthropic / Claude       | `claude-sonnet-5`   | `https://api.anthropic.com/v1/`                          |

`.env` is git-ignored and must never be committed.

### Install & run the verify script

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/verify_providers.py
```

The script builds both providers from your `.env`, sends one test prompt and one
test embedding call, and prints the results. The first run of the local
embedding backend downloads the BGE-M3 weights (~2.2 GB), so it may take a
minute. Exit code `0` means both checks passed.
