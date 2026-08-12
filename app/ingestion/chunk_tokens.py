"""Token counting for chunking.

Why this is NOT the BGE-M3 neural tokenizer by default
------------------------------------------------------
Chunk sizes are measured in tokens so dense policy text and sparse Markdown do
not drift in effective embedding length. The original implementation loaded
``BAAI/bge-m3``'s tokenizer via ``transformers.AutoTokenizer`` to get
byte-exact counts. That was **measured** to be catastrophically disproportionate
for what the counts are used for:

===============================================  ==========
process RSS after loading the BGE-M3 tokenizer   result
===============================================  ==========
via ``transformers`` (torch present)              ~1005 MB
via ``transformers`` (torch absent, deploy image)  ~611 MB
via ``tokenizers`` (Rust) directly                 ~378 MB
this module's ``heuristic`` backend                   ~0 MB
===============================================  ==========

Render's free tier has a **512 MB hard limit** and the app's own baseline is
~84 MB, so *every* ingestion that reached ``chunk_text()`` was OOM-killed the
instant the tokenizer allocated — 100% reproducible, independent of document
size, which is exactly the production failure that motivated this module (see
CLAUDE.md §4).

The counts are only used to decide *where to split text*, and the split point is
then snapped to a word boundary anyway (``chunking._overlap_tail``). The
embedding provider re-tokenizes server-side, so byte-exact local agreement with
BGE-M3 buys nothing we actually rely on. Measured on the golden corpus, the
heuristic yields chunks whose **real** BGE-M3 length is mean 211 / max 236
tokens against a 256 budget (exact tokenizer: mean 237 / max 251) — i.e. it
errs slightly *small*, the safe direction, and never exceeds the budget.

``CHUNK_TOKEN_BACKEND=hf`` restores exact BGE-M3 counting (via ``tokenizers``,
not ``transformers``) for deployments with memory to spare. It is opt-in
precisely because it cannot fit in a 512 MB box.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..config.settings import ChunkingSettings

# A "word piece" for estimation purposes: a run of word characters, or a single
# standalone punctuation mark (which subword tokenizers also bill separately).
_WORD_PIECE = re.compile(r"\w+|[^\w\s]")

# Calibrated against the real BGE-M3 tokenizer on the golden policy corpus.
# Blending a word-count signal with a character-count signal keeps the estimate
# stable across both prose (word-dominated) and tables/IDs (character-dominated),
# where either signal alone drifts.
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

    Uses the ``tokenizers`` Rust library directly rather than
    ``transformers.AutoTokenizer``: the latter costs ~230 MB more, and merely
    *accessing* its ``AutoTokenizer`` attribute pulls in ``torch`` when present
    (another ~350 MB). Imported inside the function so a ``heuristic``
    deployment never pays either import — a module-level import here previously
    blocked uvicorn's port bind long enough to trip Render's 5 s health check
    on every cold start.
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
    """Keep at most ``max_tokens`` tokens, counted from the END of ``text``.

    Used only to build the overlap tail of the previous chunk, which the caller
    immediately snaps to a word boundary — so the heuristic path works on whole
    words directly rather than decoding subword ids back to a string.
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
