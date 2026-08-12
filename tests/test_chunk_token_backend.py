"""Chunk token counting must not require a multi-hundred-MB neural tokenizer.

This pins the fix for the production root cause: loading BGE-M3's tokenizer
took the process to ~611MB (deploy image, no torch) against Render's 512MB hard
limit, so EVERY ingestion that reached ``chunk_text()`` was OOM-killed —
independent of document size. Chunking now defaults to a calibrated
zero-dependency estimator.

The important properties, in order:
  1. Chunking never imports ``transformers``/``tokenizers`` under the default.
  2. Chunks stay within the configured token budget (the estimator errs SMALL).
  3. The exact ``hf`` backend is still available for memory-rich deployments.

No DB, no network (the default path downloads nothing).
"""

from __future__ import annotations

import sys

import pytest

from app.config.settings import ChunkingSettings
from app.ingestion.chunk_tokens import count_tokens, truncate_to_tokens
from app.ingestion.chunking import chunk_text

POLICY_PROSE = (
    "Full-time employees are entitled to 25 days of paid annual leave per year, "
    "accrued monthly. Annual leave must be requested at least two weeks in "
    "advance through the HR portal. Up to 5 unused annual leave days may be "
    "carried over into the next calendar year.\n\n"
) * 12


def test_default_backend_is_the_heuristic_estimator():
    assert ChunkingSettings.from_env().token_backend == "heuristic"


def test_chunking_never_loads_a_neural_tokenizer_under_the_default(monkeypatch):
    """The OOM regression guard.

    A module-level or eagerly-triggered ``transformers``/``tokenizers`` import
    is what made ingestion impossible on a 512MB instance, so assert the heavy
    packages stay absent from ``sys.modules`` across a real chunking run.
    """
    monkeypatch.delenv("CHUNK_TOKEN_BACKEND", raising=False)
    for heavy in ("transformers", "tokenizers", "torch"):
        monkeypatch.delitem(sys.modules, heavy, raising=False)

    chunks = chunk_text(POLICY_PROSE, ChunkingSettings(chunk_size=256, chunk_overlap=40))

    assert chunks, "chunking must still produce chunks"
    for heavy in ("transformers", "tokenizers", "torch"):
        assert heavy not in sys.modules, (
            f"{heavy} was imported during chunking — this is the ~611MB OOM "
            "regression that crash-looped production"
        )


def test_chunks_respect_the_token_budget():
    """The estimator must not overshoot the size it is asked to enforce."""
    settings = ChunkingSettings(chunk_size=120, chunk_overlap=20)
    chunks = chunk_text(POLICY_PROSE, settings)

    assert len(chunks) > 1, "input should be large enough to split"
    for chunk in chunks:
        assert count_tokens(chunk) <= settings.chunk_size, (
            f"chunk of {count_tokens(chunk)} estimated tokens exceeds the "
            f"{settings.chunk_size}-token budget"
        )


def test_estimator_is_monotonic_and_positive():
    """Sanity properties any counter used for packing decisions must have."""
    assert count_tokens("") == 0
    assert count_tokens("word") >= 1
    short = count_tokens("a short sentence about leave")
    long = count_tokens("a short sentence about leave " * 20)
    assert long > short


def test_truncate_returns_a_tail_within_budget():
    text = "alpha bravo charlie delta echo foxtrot golf hotel india juliet " * 8
    tail = truncate_to_tokens(text, 20)

    assert tail, "must return something"
    assert text.strip().endswith(tail), "must be a TAIL, not a head"
    # Word-granular tails can land a shade over; a small overshoot is fine, a
    # runaway one would break the overlap contract.
    assert count_tokens(tail) <= 20 * 1.5


def test_truncate_returns_whole_text_when_already_short():
    assert truncate_to_tokens("three little words", 500) == "three little words"


def test_truncate_handles_degenerate_input():
    assert truncate_to_tokens("", 10) == ""
    assert truncate_to_tokens("anything", 0) == ""


@pytest.mark.network  # downloads the BGE-M3 tokenizer (~hundreds of MB RSS)
def test_hf_backend_still_available_for_memory_rich_deployments(monkeypatch):
    """The exact tokenizer remains opt-in — the fix removed the DEFAULT, not the
    capability. Marked ``network`` because it fetches the vocab and is precisely
    the memory profile that cannot run on the free tier."""
    pytest.importorskip("tokenizers")
    monkeypatch.setenv("CHUNK_TOKEN_BACKEND", "hf")

    assert ChunkingSettings.from_env().token_backend == "hf"
    exact = count_tokens("Full-time employees are entitled to 25 days of leave.")
    assert exact > 0
