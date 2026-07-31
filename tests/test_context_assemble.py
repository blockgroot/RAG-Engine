"""Unit tests for prompt context assembly (latency trim)."""

from __future__ import annotations

from app.rag.context_assemble import assemble_context_texts


def test_assemble_keeps_order_and_respects_budget():
    texts = ["aaaa", "bbbb", "cccc"]
    out = assemble_context_texts(texts, max_chars=10)
    assert out[0] == "aaaa"
    assert sum(len(t) for t in out) <= 10


def test_assemble_truncates_last_chunk_with_ellipsis():
    out = assemble_context_texts(["hello world " * 20], max_chars=100)
    assert len(out) == 1
    assert out[0].endswith("…")
    assert len(out[0]) <= 100


def test_assemble_unlimited_when_max_nonpositive():
    texts = ["one", "two"]
    assert assemble_context_texts(texts, max_chars=0) == texts
