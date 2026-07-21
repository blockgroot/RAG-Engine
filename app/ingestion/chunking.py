"""Structure-aware text chunking with configurable size and overlap.

Why these defaults (chunk_size=1000 chars, chunk_overlap=150 chars)
------------------------------------------------------------------
- **Retrieval precision.** ~1000 characters is roughly 200-250 tokens — small
  enough that a chunk is about *one* idea, so a similarity hit is specific
  (a leave-policy clause, not a whole handbook page). Chunks that are too large
  dilute the embedding and drag in irrelevant text; too small and they lose the
  context needed to answer.
- **Well within the model limit.** BGE-M3 accepts up to 8192 tokens, so 1000
  chars is comfortable and leaves headroom.
- **Overlap of ~15%.** A sentence answering a question often sits right on a
  chunk boundary. Repeating the last ~150 characters of the previous chunk at
  the start of the next means a boundary-straddling fact still lands whole in at
  least one chunk. 15% is the common rule-of-thumb sweet spot between recall and
  storage/duplication cost.

These are a deliberate *starting point*, tunable via CHUNK_SIZE / CHUNK_OVERLAP
without code changes. They can be revisited once we can measure retrieval quality
on real policy documents.

Strategy
--------
Split on natural boundaries first (paragraphs), keeping structural blocks such as
Markdown tables and headings whole. Only blocks that are themselves larger than
``chunk_size`` are hard-split (on sentence, then word boundaries). Blocks are
then greedily packed into chunks up to ``chunk_size``, with a character overlap
carried from the tail of the previous chunk.
"""

from __future__ import annotations

import re

from ..config.settings import ChunkingSettings
from ..core.exceptions import ConfigurationError

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

    # 1) Break into paragraph blocks; hard-split any block bigger than a chunk.
    pieces: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block.strip():
            continue
        if len(block) <= size:
            pieces.append(block)
        else:
            pieces.extend(_hard_split(block, size))

    # 2) Greedily pack pieces into chunks, carrying overlap between them.
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > size:
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
        if len(sentence) > size:
            # Sentence itself too long — fall back to words.
            for word in sentence.split(" "):
                if current and len(current) + 1 + len(word) > size:
                    out.append(current)
                    current = word
                else:
                    current = word if not current else f"{current} {word}"
        elif current and len(current) + 1 + len(sentence) > size:
            out.append(current)
            current = sentence
        else:
            current = sentence if not current else f"{current} {sentence}"

    if current:
        out.append(current)
    return out


def _overlap_tail(text: str, overlap: int) -> str:
    """Return the last ``overlap`` characters, snapped to a word boundary."""
    if overlap <= 0 or len(text) <= overlap:
        return text if overlap > 0 else ""
    tail = text[-overlap:]
    # Avoid starting the overlap mid-word.
    space = tail.find(" ")
    if space != -1:
        tail = tail[space + 1 :]
    return tail.strip()
