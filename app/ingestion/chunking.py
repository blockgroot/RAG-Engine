"""Structure-aware text chunking with configurable size and overlap (token-based).

Defaults: 256 tokens (about one idea, specific on a hit) with 40 overlap, so
boundary facts land whole somewhere. Counted with the heuristic counter, not
the BGE tokenizer. Split on paragraphs, then sentences, then words, packed
greedily with overlap.

``CHUNK_MAX_CHARS`` is the last-resort character cap: a whitespace-free run
(base64, a long signed URL) has no linguistic break and bills one token per
character, and a 48KB one failed a whole Drive ingest at the embedder.

An oversized Markdown table is split by row with its header repeated, so
every piece keeps its column meaning.
"""

from __future__ import annotations

import re

from ..config.settings import ChunkingSettings
from ..core.exceptions import ConfigurationError
from .chunk_tokens import count_tokens, truncate_to_tokens

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_TABLE_CELL_SEP = re.compile(r"^:?-{1,}:?$")


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
            pieces.extend(_hard_split_block(block, size, max_chars))

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

    # Overlap can push a chunk over; the character cap must hold for every
    # chunk the embedder sees.
    return [
        slice_ for chunk in chunks for slice_ in _split_oversized(chunk, max_chars)
    ]


def _split_oversized(text: str, max_chars: int) -> list[str]:
    """Slice ``text`` into <= ``max_chars`` pieces, preferring whitespace breaks.

    Last resort; an unbroken run is cut at exactly ``max_chars``.
    """
    if len(text) <= max_chars:
        return [text] if text else []

    out: list[str] = []
    rest = text
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = window.rstrip().rfind(" ")
        # Only a break in the last third, or pieces get absurdly short.
        if cut < max_chars // 3:
            cut = max_chars
        out.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()

    if rest:
        out.append(rest)
    return [piece for piece in out if piece]


def _is_table_separator_row(line: str) -> bool:
    """True for a GFM table separator row, e.g. ``|---|---|``. Needs a ``|``,
    so a ``---`` rule or heading underline is not one.
    """
    line = line.strip()
    if not line or "|" not in line or "-" not in line:
        return False
    cells = [c.strip() for c in line.strip("|").split("|")]
    return bool(cells) and all(_TABLE_CELL_SEP.match(c) for c in cells)


def _find_markdown_table_start(lines: list[str]) -> int | None:
    """Index of a table's header line (the line right before its separator row)."""
    for i in range(len(lines) - 1):
        if "|" in lines[i] and _is_table_separator_row(lines[i + 1]):
            return i
    return None


def _hard_split_block(text: str, size: int, max_chars: int) -> list[str]:
    """Split an oversized block, by whole row (header repeated) when it holds
    a Markdown table; otherwise ``_hard_split``.
    """
    lines = text.split("\n")
    start = _find_markdown_table_start(lines)
    if start is None:
        return _hard_split(text, size, max_chars)

    prefix = "\n".join(line for line in lines[:start] if line.strip()).strip()
    header_block = f"{lines[start]}\n{lines[start + 1]}"
    data_rows = [line for line in lines[start + 2 :] if line.strip()]
    if not data_rows:
        return _hard_split(text, size, max_chars)

    def fits(rows: list[str]) -> bool:
        candidate = header_block + "\n" + "\n".join(rows)
        return count_tokens(candidate) <= size and len(candidate) <= max_chars

    pieces: list[str] = []
    current: list[str] = []
    for row in data_rows:
        if fits(current + [row]):
            current.append(row)
            continue
        if current:
            pieces.append(header_block + "\n" + "\n".join(current))
            current = []
        if fits([row]):
            current = [row]
        else:
            # A row too wide for the header beside it: cut on characters.
            room = max(max_chars - len(header_block) - 1, 80)
            for sub in _split_oversized(row, room):
                pieces.append(f"{header_block}\n{sub}")
    if current:
        pieces.append(header_block + "\n" + "\n".join(current))

    if prefix:
        pieces[0] = f"{prefix}\n\n{pieces[0]}"
    return pieces


def _hard_split(text: str, size: int, max_chars: int) -> list[str]:
    """Split an oversized block on sentence, then word, then character
    boundaries (``max_chars`` for text with no word break).
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
