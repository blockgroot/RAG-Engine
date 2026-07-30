"""Unit tests for corpus-vocab query spelling (Phase 17)."""

from __future__ import annotations

from app.config.settings import QueryNormSettings
from app.rag.query_normalize import CorpusSpellNormalizer, build_vocab_counts


CORPUS = [
    "Employees may buy protein supplements with the health allowance.",
    "Business travel expenses are reimbursed up to $500 per trip.",
    "Full-time employees get 25 days of paid annual leave.",
    "The main office handles leave requests. Five training modules apply.",
    "Company dental coverage is provided for full-time staff.",
]

_ON = QueryNormSettings(enabled=True, max_edit_distance=1, min_word_length=4)


def test_vocab_extracts_policy_terms():
    counts = build_vocab_counts(CORPUS, min_word_length=4)
    assert "protein" in counts
    assert "supplements" in counts
    assert "reimbursed" in counts
    assert "leave" in counts


def test_normalizer_fixes_typos_toward_corpus():
    norm = CorpusSpellNormalizer(_ON)
    out = norm.normalize(
        "are protien suppliments reimbersed?",
        "org-1",
        CORPUS,
    )
    low = out.lower()
    assert "protein" in low
    assert "supplements" in low
    assert "reimbursed" in low
    assert "protien" not in low


def test_normalizer_leaves_clean_question_unchanged():
    norm = CorpusSpellNormalizer(_ON)
    q = "How many days of paid annual leave do full-time employees get?"
    assert norm.normalize(q, "org-1", CORPUS) == q


def test_normalizer_does_not_map_many_onto_rare_corpus_main():
    """Regression: 'many' must not become 'main' just because policies say 'main'."""
    norm = CorpusSpellNormalizer(_ON)
    q = "How many days of paid annual leave do full-time employees get?"
    assert norm.normalize(q, "org-main", CORPUS) == q
    assert "many" in norm.normalize("how meny days", "org-main", CORPUS).lower()


def test_normalizer_disabled_is_noop():
    norm = CorpusSpellNormalizer(
        QueryNormSettings(enabled=False, max_edit_distance=1, min_word_length=4)
    )
    q = "protien suppliments"
    assert norm.normalize(q, "org-1", CORPUS) == q


def test_normalizer_does_not_force_corpus_inflection():
    """Corpus 'provided' must not rewrite a clean 'provide'."""
    norm = CorpusSpellNormalizer(_ON)
    q = "What health and dental insurance plan does the company provide?"
    assert norm.normalize(q, "org-inflect", CORPUS) == q


def test_normalizer_preserves_external_entity_names():
    """Phase 5 web-search depends on named entities surviving intact.

    Distance-2 SymSpell previously mapped Niva→five and Compare→company against
    a policy vocab containing those words. Entities must not be "corrected."
    """
    norm = CorpusSpellNormalizer(_ON)
    cases = [
        "What does Cigna health insurance generally cover?",
        "What is Niva Bupa's claim settlement ratio?",
        "Compare Cigna and UnitedHealthcare dental plans",
    ]
    for q in cases:
        assert norm.normalize(q, "org-entity", CORPUS) == q, q


def test_distance_two_entity_corruption_is_blocked_even_if_enabled():
    """Capitalized OOV skip still protects entities if max_edit_distance is raised."""
    norm = CorpusSpellNormalizer(
        QueryNormSettings(enabled=True, max_edit_distance=2, min_word_length=4)
    )
    q = "What is Niva Bupa's claim settlement ratio?"
    out = norm.normalize(q, "org-entity-d2", CORPUS)
    assert "Niva" in out
    assert "Bupa" in out
    assert "five" not in out.lower()
