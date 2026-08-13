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


# --- The character ceiling (CHUNK_MAX_CHARS) -------------------------------
#
# Production failure this pins: a Drive sync died with the embedding endpoint's
# 400 "Input text exceeds the model's maximum of 8194 tokens". Cause was NOT the
# token budget being wrong — it was that every splitter here works on a
# linguistic boundary (paragraph, sentence, word), and a whitespace-free run has
# none, so it was emitted as one arbitrarily large chunk. Measured against the
# real BGE-M3 tokenizer, base64 bills 1 token per character, so no token
# estimate could have caught it either; characters are the bound that holds.

_EMBED_TOKEN_LIMIT = 8192


def test_a_whitespace_free_blob_is_split_instead_of_emitted_whole():
    """A base64 data URI (as exported Google Docs contain) has no word break."""
    blob = "data:image/png;base64," + "A1b2C3d4" * 6000  # 48KB, zero spaces
    chunks = chunk_text(blob)

    assert len(chunks) > 1, "an unbreakable run must still be split"
    for ch in chunks:
        assert len(ch) <= 4000
        # 1 token/char is the worst case any text can reach, so bounding chars
        # bounds real tokens — this is the property the embedding call needs.
        assert len(ch) <= _EMBED_TOKEN_LIMIT


def test_unsegmented_text_without_spaces_is_bounded():
    """CJK prose carries no spaces at all; the old splitter returned one chunk."""
    for text in ("政策条款说明" * 3000, "x" * 300_000):
        chunks = chunk_text(text)
        assert max(len(ch) for ch in chunks) <= 4000


def test_the_ceiling_is_configurable_and_respected():
    settings = ChunkingSettings(chunk_size=256, chunk_overlap=40, max_chunk_chars=500)
    chunks = chunk_text("A1b2C3d4" * 2000, settings)
    assert chunks
    assert max(len(ch) for ch in chunks) <= 500


def test_the_ceiling_never_fires_on_ordinary_prose():
    """It is a backstop, not a second size knob: real documents must be untouched."""
    prose = (
        "Full-time employees receive 25 days of paid leave each year. "
        "Unused days may carry over to the following year. "
    ) * 200
    chunks = chunk_text(prose)
    # A legitimate 256-token prose chunk is ~1000-1300 chars, well under 4000,
    # so no chunk should be sitting exactly at the ceiling.
    assert max(len(ch) for ch in chunks) < 4000
    for ch in chunks:
        assert count_tokens(ch) <= 256 + 40  # token budget still the binding one


def test_a_long_spaced_run_is_cut_at_whitespace_not_mid_word():
    """Degrade gracefully: only genuinely unbroken text gets cut mid-token."""
    settings = ChunkingSettings(chunk_size=100_000, chunk_overlap=0, max_chunk_chars=300)
    chunks = chunk_text("alpha bravo charlie delta echo " * 100, settings)
    for ch in chunks:
        assert ch == ch.strip()
        for word in ch.split():
            assert word in {"alpha", "bravo", "charlie", "delta", "echo"}
