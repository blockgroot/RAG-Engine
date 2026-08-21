"""Layout-aware chunking: Markdown tables split by row, not by sentence/word.

Smart-chunking gap #1 (docs/RAG_VIDEO_COMPARISON.md). Before this, an
oversized table block fell into ``_hard_split``, which has no notion of a
table row — it would explode the table word-by-word across chunks, losing
column headers and row alignment entirely. Now an oversized table is packed
by whole row, with its header + separator row repeated on every resulting
piece so a chunk holding only a middle slice of rows still carries its
column meaning.
"""

from __future__ import annotations

from app.config.settings import ChunkingSettings
from app.ingestion.chunking import chunk_text

HEADER = "| Tenure | Leave days |"
SEPARATOR = "|---|---|"


def _table(n_rows: int) -> str:
    rows = [f"| {i} years | {10 + i} days |" for i in range(n_rows)]
    return "\n".join([HEADER, SEPARATOR] + rows)


def test_small_table_that_fits_budget_is_untouched():
    settings = ChunkingSettings.from_env()
    table = _table(3)
    chunks = chunk_text(table, settings)
    assert chunks == [table]


def test_oversized_table_splits_by_row_not_by_word():
    settings = ChunkingSettings(chunk_size=60, chunk_overlap=0, max_chunk_chars=4000)
    table = _table(20)
    chunks = chunk_text(table, settings)

    assert len(chunks) > 1, "a 20-row table at this budget must split"
    for ch in chunks:
        # Every piece is still whole rows of the ORIGINAL table shape — never
        # a bare fragment of a cell (the old word-splitting failure mode).
        for line in ch.splitlines():
            line = line.strip()
            if not line:
                continue
            assert line in (HEADER, SEPARATOR) or (
                line.startswith("|") and line.endswith("|") and line.count("|") == 3
            ), f"row was not preserved whole: {line!r}"


def test_oversized_table_repeats_header_on_every_piece():
    settings = ChunkingSettings(chunk_size=60, chunk_overlap=0, max_chunk_chars=4000)
    table = _table(20)
    chunks = chunk_text(table, settings)

    assert len(chunks) > 1
    for ch in chunks:
        assert HEADER in ch and SEPARATOR in ch


def test_every_row_appears_in_exactly_one_or_more_chunks_no_row_lost():
    settings = ChunkingSettings(chunk_size=60, chunk_overlap=0, max_chunk_chars=4000)
    table = _table(20)
    chunks = chunk_text(table, settings)
    combined = "\n".join(chunks)

    for i in range(20):
        assert f"| {i} years | {10 + i} days |" in combined


def test_heading_immediately_before_a_table_is_preserved_as_situating_prefix():
    """A heading with no blank line before the table stays attached to the
    first resulting piece, so the table doesn't lose its section context."""
    settings = ChunkingSettings(chunk_size=60, chunk_overlap=0, max_chunk_chars=4000)
    doc = "## Leave entitlement by tenure\n" + _table(20)
    chunks = chunk_text(doc, settings)

    assert "Leave entitlement by tenure" in chunks[0]
    assert HEADER in chunks[0]


def test_pathologically_wide_single_row_is_bounded_by_max_chars():
    """A single row wider than max_chars alone must still be cut, never emitted whole."""
    settings = ChunkingSettings(chunk_size=60, chunk_overlap=0, max_chunk_chars=200)
    wide_row = "| " + ("x" * 500) + " |"
    table = "\n".join([HEADER, SEPARATOR, wide_row])

    chunks = chunk_text(table, settings)

    assert all(len(ch) <= 200 for ch in chunks)


def test_a_short_table_within_a_larger_oversized_prose_block_is_not_misdetected():
    """A stray '|' in ordinary prose, with an unrelated '---' hr below it,
    must never be mistaken for a table (no pipe in the separator line)."""
    settings = ChunkingSettings(chunk_size=15, chunk_overlap=0, max_chunk_chars=4000)
    text = (
        "Choose an option: yes | no | maybe, and continue reading this "
        "sentence well past the token budget so it must hard-split.\n"
        "---\n"
        "More unrelated prose follows after a horizontal rule line here."
    )
    chunks = chunk_text(text, settings)
    # No row-doubling artifact (e.g. a repeated header) — plain hard-split behaviour.
    assert not any("|---|" in ch for ch in chunks)
