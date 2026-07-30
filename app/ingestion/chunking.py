"""Structure-aware text chunking with configurable size and overlap (token-based).

Why these defaults (chunk_size=256 tokens, chunk_overlap=40 tokens)
-------------------------------------------------------------------
- **Retrieval precision.** ~256 tokens is roughly one policy idea — small enough
  that a similarity hit is specific, large enough to retain answering context.
- **Aligned with BGE-M3.** Sizes are counted with the same tokenizer the embedding
  model uses, so chunk density no longer drifts with character-heavy tables vs
  sparse prose.
- **Overlap of ~15%.** Boundary-straddling facts still land whole in at least one
  chunk without excessive duplication.

Tunable via ``CHUNK_SIZE`` / ``CHUNK_OVERLAP`` (token counts).

Strategy
--------
Split on natural boundaries first (paragraphs). Only blocks larger than
``chunk_size`` tokens are hard-split (sentence, then word boundaries). Pieces
are greedily packed up to ``chunk_size`` tokens, with token overlap from the
tail of the previous chunk.
"""

from __future__ import annotations

import re

from ..config.settings import ChunkingSettings
from ..core.exceptions import ConfigurationError
from .chunk_tokens import count_tokens, truncate_to_tokens

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def chunk_text(text: str, settings: ChunkingSettings | None = None) -> list[str]:
    """Split preprocessed text into overlapping, structure-aware chunks."""
    settings = settings or ChunkingSettings.from_env()
    size = settings.chunk_size
    overlap = settings.chunk_overlap

    if size <= 0:
        raise ConfigurationError("CHUNK_SIZE must be positive")
    if overlap < 0 or overlap >= size:
        raise ConfigurationError("CHUNK_OVERLAP must be >= 0 and < CHUNK_SIZE")

    text = text.strip()
    if not text:
        return []

    pieces: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block.strip():
            continue
        if count_tokens(block) <= size:
            pieces.append(block)
        else:
            pieces.extend(_hard_split(block, size))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and count_tokens(current) + 2 + count_tokens(piece) > size:
            chunks.append(current)
            tail = _overlap_tail(current, overlap)
            current = f"{tail}\n\n{piece}" if tail else piece
        else:
            current = piece if not current else f"{current}\n\n{piece}"

    if current:
        chunks.append(current)

    return chunks


def _hard_split(text: str, size: int) -> list[str]:
    """Split an oversized block on sentence, then word, boundaries."""
    out: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        if count_tokens(sentence) > size:
            for word in sentence.split(" "):
                if current and count_tokens(current) + 1 + count_tokens(word) > size:
                    out.append(current)
                    current = word
                else:
                    current = word if not current else f"{current} {word}"
        elif current and count_tokens(current) + 1 + count_tokens(sentence) > size:
            out.append(current)
            current = sentence
        else:
            current = sentence if not current else f"{current} {sentence}"

    if current:
        out.append(current)
    return out


def _overlap_tail(text: str, overlap_tokens: int) -> str:
    """Return the last ``overlap_tokens`` tokens, snapped to a word boundary."""
    if overlap_tokens <= 0:
        return ""
    if count_tokens(text) <= overlap_tokens:
        return text
    tail = truncate_to_tokens(text, overlap_tokens)
    space = tail.find(" ")
    if space != -1:
        tail = tail[space + 1 :]
    return tail.strip()
