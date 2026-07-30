"""Token counting for chunking aligned with the embedding model (BGE-M3).

Chunk sizes are measured in tokens so dense policy text and sparse Markdown
do not drift in effective embedding length. Uses the same HuggingFace tokenizer
as ``BAAI/bge-m3`` (lazy-loaded once per process).
"""

from __future__ import annotations

from functools import lru_cache

from transformers import AutoTokenizer


@lru_cache(maxsize=1)
def _tokenizer() -> AutoTokenizer:
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
