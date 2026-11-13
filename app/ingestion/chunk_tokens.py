"""Token counting for chunking: a heuristic, not the BGE-M3 tokenizer.

Loading the real tokenizer costs 378 MB to 1 GB of RSS and OOM-killed every
ingest on a 512 MB host. The counts only pick split points (snapped to words),
and the heuristic errs small (real length mean 211 / max 236 against 256).
``CHUNK_TOKEN_BACKEND=hf`` opts into exact counting where memory allows.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..config.settings import ChunkingSettings

# A "word piece" for estimation purposes: a run of word characters, or a single
# standalone punctuation mark (which subword tokenizers also bill separately).
_WORD_PIECE = re.compile(r"\w+|[^\w\s]")

# Calibrated against BGE-M3. Words plus characters stays stable across prose
# and tables/IDs, where either alone drifts.
_WORDS_TO_TOKENS = 1.30
_CHARS_PER_TOKEN = 4.0
_WORD_WEIGHT = 0.75
_CHAR_WEIGHT = 0.25


def _estimate_tokens(text: str) -> int:
    words = len(_WORD_PIECE.findall(text))
    blended = (
        _WORD_WEIGHT * words * _WORDS_TO_TOKENS
        + _CHAR_WEIGHT * len(text) / _CHARS_PER_TOKEN
    )
    return max(1, round(blended))


@lru_cache(maxsize=1)
def _hf_tokenizer():
    """Exact BGE-M3 tokenizer, loaded lazily and only for ``backend="hf"``.

    ``tokenizers`` directly, not ``transformers`` (hundreds of MB more, and it
    pulls in torch). Lazy: a module-level import delayed the port bind past
    the health check.
    """
    from tokenizers import Tokenizer

    return Tokenizer.from_pretrained("BAAI/bge-m3")


def _backend() -> str:
    return ChunkingSettings.from_env().token_backend


def count_tokens(text: str) -> int:
    """Return the approximate (or exact, under ``hf``) token count of ``text``."""
    if not text:
        return 0
    if _backend() == "hf":
        return len(_hf_tokenizer().encode(text, add_special_tokens=False).ids)
    return _estimate_tokens(text)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Keep at most ``max_tokens`` tokens from the end of ``text`` (the
    overlap tail; the heuristic path works on whole words).
    """
    if max_tokens <= 0 or not text:
        return ""

    if _backend() == "hf":
        tok = _hf_tokenizer()
        ids = tok.encode(text, add_special_tokens=False).ids
        if len(ids) <= max_tokens:
            return text
        return tok.decode(ids[-max_tokens:], skip_special_tokens=True).strip()

    if _estimate_tokens(text) <= max_tokens:
        return text
    words = text.split()
    if not words:
        return ""
    kept: list[str] = []
    for word in reversed(words):
        kept.append(word)
        if _estimate_tokens(" ".join(reversed(kept))) >= max_tokens:
            break
    return " ".join(reversed(kept)).strip()
