# RAG Engine

A multi-tenant RAG policy Q&A platform, built in small, reviewable phases.

> **Current phase:** LLM & Embedding provider abstraction layer only.
> The RAG pipeline, database schema, agents, adapters, and orchestrator are
> future phases and are intentionally **not** built yet.

## Provider abstraction layer

This module wraps the official [`openai`](https://pypi.org/project/openai/)
Python client so the rest of the application can talk to any OpenAI-compatible
endpoint (FreeLLMAPI, DeepInfra, OpenAI itself, …) through one small, stable
interface — without ever touching provider-specific details directly.

### Layout

```
app/
  providers/
    __init__.py        # public exports
    llm.py             # LLMProvider.generate(prompt) -> str  (HTTP)
    embeddings.py      # EmbeddingProvider.embed(texts) -> ...  (HTTP, OpenAI-compatible)
    local_embeddings.py# LocalEmbeddingProvider.embed(texts) -> ...  (in-process)
    exceptions.py      # ProviderError + specific subclasses
scripts/
  verify_providers.py  # manual smoke test for the providers
.env.example           # expected environment variables
```

- **`LLMProvider`** — `generate(prompt: str) -> str`. Calls
  `chat.completions.create` and returns the reply text.
- **`LocalEmbeddingProvider`** — `embed(texts: list[str]) -> list[list[float]]`.
  Runs BGE-M3 **locally in-process** via `sentence-transformers`: $0, no API
  key, and document text never leaves the machine. This is the default used by
  the verify script.
- **`EmbeddingProvider`** — same `embed(...)` signature, but talks to a remote
  OpenAI-compatible embeddings endpoint (e.g. DeepInfra) over HTTP. Kept as an
  alternative for when you'd rather offload embedding to a hosted service.

Both embedding classes expose the identical `embed(...)` method, so the rest of
the app can swap between local and hosted without any code changes. All
providers convert underlying errors (timeouts, connection failures, model load
failures, API errors) into a clear custom `ProviderError` so callers only need
to catch one exception type.

### Configuration

Copy the example env file and fill in real values:

```bash
cp .env.example .env
```

| Variable             | Purpose                          | Example                                 |
| -------------------- | -------------------------------- | --------------------------------------- |
| `LLM_BASE_URL`       | FreeLLMAPI OpenAI-compatible URL | `http://localhost:3001/v1`              |
| `LLM_API_KEY`        | FreeLLMAPI key                   | `freellmapi-…`                          |
| `LLM_MODEL`          | Model id                         | `auto`                                  |
| `EMBEDDING_MODEL`    | Local embedding model            | `BAAI/bge-m3`                           |
| `EMBEDDING_DEVICE`   | Optional device override         | `cpu` / `cuda` / `mps` (auto if unset)  |

The local embedding provider needs no key or URL — it loads the model in-process
and downloads it once on first run. (The hosted `EmbeddingProvider` alternative
uses `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` instead.)

`.env` is git-ignored and must never be committed.

### Install & run the verify script

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/verify_providers.py
```

The script instantiates the LLM and local embedding providers from your `.env`,
sends one test prompt and one test embedding call, and prints the results. The
first run downloads the BGE-M3 weights (~2.2 GB), so it may take a minute. Exit
code `0` means both checks passed.
