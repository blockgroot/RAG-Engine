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
    llm.py             # LLMProvider.generate(prompt) -> str
    embeddings.py      # EmbeddingProvider.embed(texts) -> list[list[float]]
    exceptions.py      # ProviderError + specific subclasses
scripts/
  verify_providers.py  # manual smoke test for both providers
.env.example           # expected environment variables
```

- **`LLMProvider`** — `generate(prompt: str) -> str`. Calls
  `chat.completions.create` and returns the reply text.
- **`EmbeddingProvider`** — `embed(texts: list[str]) -> list[list[float]]`.
  Calls `embeddings.create` and returns one vector per input, in order.

Both read their config (`api_key`, `base_url`, `model`) from constructor
arguments, falling back to environment variables. Both convert underlying
`openai` errors (timeouts, connection failures, API errors) into a clear
custom `ProviderError` so callers only need to catch one exception type.

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
| `EMBEDDING_BASE_URL` | DeepInfra OpenAI-compatible URL  | `https://api.deepinfra.com/v1/openai`   |
| `EMBEDDING_API_KEY`  | DeepInfra key                    | `…`                                     |
| `EMBEDDING_MODEL`    | Embedding model                  | `BAAI/bge-m3`                           |

`.env` is git-ignored and must never be committed.

### Install & run the verify script

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python scripts/verify_providers.py
```

The script instantiates both providers from your `.env`, sends one test prompt
and one test embedding call, and prints the results. Exit code `0` means both
checks passed.
