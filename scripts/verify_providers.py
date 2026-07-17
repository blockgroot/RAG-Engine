"""Manual smoke test for the provider abstraction layer.

Loads config from `.env`, instantiates both providers, and runs one real call
against each so you can confirm they are wired correctly before building on top.

Run:
    python scripts/verify_providers.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/verify_providers.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.providers import (
    LLMProvider,
    LocalEmbeddingProvider,
    ProviderError,
)


def check_llm() -> bool:
    print("== LLM ==")
    try:
        llm = LLMProvider()
        print(f"  base_url : {llm.base_url}")
        print(f"  model    : {llm.model}")
        reply = llm.generate("Reply with exactly: provider layer OK")
        print(f"  response : {reply.strip()!r}")
        print("  LLM check passed.\n")
        return True
    except ProviderError as exc:
        print(f"  LLM check FAILED: {exc}")
        if exc.cause:
            print(f"  cause: {exc.cause}")
        print()
        return False


def check_embeddings() -> bool:
    print("== Embeddings (local sentence-transformers) ==")
    try:
        embedder = LocalEmbeddingProvider()
        print(f"  model    : {embedder.model_name}")
        print(f"  device   : {embedder.device or 'auto'}")
        print("  (first run downloads the model; this can take a minute)")
        vectors = embedder.embed(["hello world", "second document"])
        print(f"  inputs   : 2")
        print(f"  vectors  : {len(vectors)} returned")
        if vectors:
            print(f"  dim      : {len(vectors[0])}")
            print(f"  preview  : {vectors[0][:5]}")
        print("  Embedding check passed.\n")
        return True
    except ProviderError as exc:
        print(f"  Embedding check FAILED: {exc}")
        if exc.cause:
            print(f"  cause: {exc.cause}")
        print()
        return False


def main() -> int:
    load_dotenv()
    llm_ok = check_llm()
    emb_ok = check_embeddings()

    if llm_ok and emb_ok:
        print("All provider checks passed.")
        return 0
    print("One or more provider checks failed. See output above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
