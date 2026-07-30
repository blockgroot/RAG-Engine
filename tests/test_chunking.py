"""Token-based chunking tests (Phase 18)."""

from __future__ import annotations

import re

from app.config.settings import ChunkingSettings
from app.ingestion.chunking import chunk_text
from app.ingestion.chunk_tokens import count_tokens

_SENTENCE_END = re.compile(r"[.!?][\s\"']*$")


def test_token_chunk_respects_max_size_and_overlap():
    text = "\n\n".join(
        f"Paragraph {i}. " + "Policy detail sentence. " * 12 for i in range(8)
    )
    settings = ChunkingSettings(chunk_size=80, chunk_overlap=12)
    chunks = chunk_text(text, settings)
    assert len(chunks) >= 2
    for ch in chunks:
        assert count_tokens(ch) <= 80 + 5  # small tokenizer boundary slack
    # Overlap: tail of chunk N appears at start of chunk N+1 (token overlap).
    for a, b in zip(chunks, chunks[1:]):
        tail_words = a.split()[-8:]
        assert any(w in b for w in tail_words if len(w) > 3)


def test_no_mid_sentence_hard_split_on_periods():
    paragraph = (
        "Full-time employees receive 25 days of paid leave. "
        "Part-time employees receive 12 days, pro-rated. "
        "Unused days may carry over."
    )
    settings = ChunkingSettings(chunk_size=40, chunk_overlap=5)
    chunks = chunk_text(paragraph, settings)
    for ch in chunks:
        assert _SENTENCE_END.search(ch.strip()) or ch == chunks[-1]


def test_empty_and_single_paragraph():
    assert chunk_text("") == []
    one = "Single short policy note."
    assert chunk_text(one) == [one]
