"""Non-LLM keyword extraction (metadata-creation gap #2, the cheap half).

Deterministic, no model call, no network — pure text-processing tests.
"""

from __future__ import annotations

from app.ingestion.keywords import append_keyword_line, extract_keywords


def test_extracts_frequent_non_trivial_terms():
    text = (
        "Part-time employees receive twelve days of paid annual leave per "
        "year, pro-rated by tenure. Annual leave requests must be submitted "
        "two weeks in advance."
    )
    kw = extract_keywords(text, top_n=6)
    assert "annual" in kw
    assert "leave" in kw
    # Common stopwords never appear.
    assert "the" not in kw and "must" not in kw and "per" not in kw


def test_respects_top_n():
    text = "alpha alpha beta beta gamma gamma delta delta epsilon epsilon"
    assert len(extract_keywords(text, top_n=2)) == 2
    assert len(extract_keywords(text, top_n=100)) == 5


def test_top_n_zero_returns_empty():
    assert extract_keywords("annual leave policy details", top_n=0) == []


def test_preserves_first_occurrence_casing():
    text = "PTO is short for paid time off. PTO requests need manager approval."
    kw = extract_keywords(text, top_n=3)
    assert "PTO" in kw
    assert "pto" not in kw


def test_deterministic_tie_break_by_first_occurrence():
    text = "zebra one apple one mango one"
    # "one" appears 3x; zebra/apple/mango appear once each, in this order.
    kw = extract_keywords(text, top_n=4)
    assert kw[0] == "one"
    assert kw[1:] == ["zebra", "apple", "mango"]


def test_empty_or_stopword_only_text_returns_empty():
    assert extract_keywords("", top_n=6) == []
    assert extract_keywords("the and but for", top_n=6) == []


def test_append_keyword_line_extracts_from_source_not_stored():
    """Extraction must use the RAW source text, not whatever's being stored —
    e.g. an LLM-added context sentence full of generic words shouldn't
    dilute which words actually get surfaced as keywords."""
    stored = "This chunk covers part-time leave. Part-time leave entitlement details."
    raw_source = "Part-time employees receive twelve days of annual leave."

    out = append_keyword_line(stored, raw_source, top_n=3)

    assert out.startswith(stored)
    assert "Keywords:" in out
    assert "annual" in out or "leave" in out


def test_append_keyword_line_is_a_noop_when_nothing_extractable():
    stored = "some stored text"
    out = append_keyword_line(stored, "the and but for", top_n=3)
    assert out == stored
