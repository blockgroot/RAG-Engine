"""Token counting for chunking aligned with the embedding model (BGE-M3).

Chunk sizes are measured in tokens so dense policy text and sparse Markdown
do not drift in effective embedding length. Uses the same HuggingFace tokenizer
as ``BAAI/bge-m3`` (lazy-loaded once per process).
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase


@lru_cache(maxsize=1)
def _tokenizer() -> "PreTrainedTokenizerBase":
    # Imported lazily, not at module scope: `transformers`' own import graph
    # (tokenizers, huggingface_hub, safetensors, numpy, ...) costs several
    # seconds even with the tokenizer pre-baked into the image (see CLAUDE.md).
    # This module is imported transitively by app/api/main.py's routers on
    # every process boot, so a module-level import paid that cost before
    # uvicorn could bind the port -- blocking /health on Render's throttled
    # free-tier CPU during every cold start/restart, independent of whether
    # an ingest job is even running.
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained("BAAI/bge-m3")


def count_tokens(text: str) -> int:
    """Return the number of tokens in ``text`` (no special tokens)."""
    if not text:
        return 0
    return len(_tokenizer().encode(text, add_special_tokens=False))


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Keep at most ``max_tokens`` tokens, decoding back to a string."""
    if max_tokens <= 0 or not text:
        return ""
    ids = _tokenizer().encode(text, add_special_tokens=False)
    if len(ids) <= max_tokens:
        return text
    return _tokenizer().decode(ids[-max_tokens:], skip_special_tokens=True).strip()
