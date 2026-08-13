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

The character backstop (``CHUNK_MAX_CHARS``)
--------------------------------------------
Every boundary above is a *linguistic* one, so text with no such boundary used
to defeat all of them: a single whitespace-free run — a base64 data URI or a
long signed googleusercontent link in an exported Google Doc, a minified blob,
an unsegmented CJK paragraph — has no paragraph, no sentence and no word break
to split on, so ``_hard_split`` emitted it whole and a 48KB blob became ONE
chunk. That reached the embedding endpoint as a single input and was rejected
outright (``INPUT_TOKEN_LIMIT_EXCEEDED``: "input text exceeds the model's
maximum of 8194 tokens"), failing the whole document's ingest — observed in
production on a Drive sync.

Note that the token count was no defence here even in principle: measured
against the real BGE-M3 tokenizer, base64 bills **1 token per character**, so
the heuristic backend under-counted that blob 16x (and a CJK run 8x). Chasing
that with a better estimator is a losing game against arbitrary input, so the
final split is on characters instead — the one measure that cannot be fooled.
Because 1 token/char is the worst case anything can reach, capping characters
caps tokens outright. Applied as a last resort only, after every linguistic
boundary has been tried, and it does not fire on real prose (see
``DEFAULT_MAX_CHUNK_CHARS``).
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
    max_chars = settings.max_chunk_chars

    if size <= 0:
        raise ConfigurationError("CHUNK_SIZE must be positive")
    if overlap < 0 or overlap >= size:
        raise ConfigurationError("CHUNK_OVERLAP must be >= 0 and < CHUNK_SIZE")
    if max_chars <= 0:
        raise ConfigurationError("CHUNK_MAX_CHARS must be positive")

    text = text.strip()
    if not text:
        return []

    pieces: list[str] = []
    for block in text.split("\n\n"):
        block = block.strip("\n")
        if not block.strip():
            continue
        if count_tokens(block) <= size and len(block) <= max_chars:
            pieces.append(block)
        else:
            pieces.extend(_hard_split(block, size, max_chars))

    chunks: list[str] = []
    current = ""
    for piece in pieces:
        over_budget = (
            count_tokens(current) + 2 + count_tokens(piece) > size
            or len(current) + 2 + len(piece) > max_chars
        )
        if current and over_budget:
            chunks.append(current)
            tail = _overlap_tail(current, overlap)
            current = f"{tail}\n\n{piece}" if tail else piece
        else:
            current = piece if not current else f"{current}\n\n{piece}"

    if current:
        chunks.append(current)

    # The overlap tail is prepended *after* the size check above, so a packed
    # chunk can still land slightly over either budget. Enforce the character
    # ceiling one final time: it is the only bound the embedding call depends
    # on, so it must hold for every chunk that leaves this function.
    return [
        slice_ for chunk in chunks for slice_ in _split_oversized(chunk, max_chars)
    ]


def _split_oversized(text: str, max_chars: int) -> list[str]:
    """Slice ``text`` into <= ``max_chars`` pieces, preferring whitespace breaks.

    The last-resort splitter, reached only when no paragraph, sentence or word
    boundary was available (or when overlap pushed a packed chunk over). Each
    window still ends at the last whitespace inside it when there is one, so a
    long-but-spaced run degrades gracefully rather than being cut mid-word; a
    genuinely unbroken run is cut at exactly ``max_chars``, which is the point.
    """
    if len(text) <= max_chars:
        return [text] if text else []

    out: list[str] = []
    rest = text
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = window.rstrip().rfind(" ")
        # Only honour a whitespace break in the last third of the window;
        # nearer the start it would emit an absurdly short piece and loop for
        # a long time on the remainder.
        if cut < max_chars // 3:
            cut = max_chars
        out.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()

    if rest:
        out.append(rest)
    return [piece for piece in out if piece]


def _hard_split(text: str, size: int, max_chars: int) -> list[str]:
    """Split an oversized block on sentence, then word, then character boundaries.

    ``max_chars`` is the escape hatch for text with no word boundary at all: a
    single 48KB base64 run used to survive every branch below and be emitted
    whole, which the embedding endpoint then rejected (see module docstring).
    """
    out: list[str] = []
    current = ""
    for sentence in _SENTENCE_SPLIT.split(text):
        if count_tokens(sentence) > size or len(sentence) > max_chars:
            for raw_word in sentence.split(" "):
                # A "word" longer than the ceiling has no internal boundary
                # left to split on — slice it on characters before packing.
                for word in _split_oversized(raw_word, max_chars):
                    if current and (
                        count_tokens(current) + 1 + count_tokens(word) > size
                        or len(current) + 1 + len(word) > max_chars
                    ):
                        out.append(current)
                        current = word
                    else:
                        current = word if not current else f"{current} {word}"
        elif current and (
            count_tokens(current) + 1 + count_tokens(sentence) > size
            or len(current) + 1 + len(sentence) > max_chars
        ):
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
