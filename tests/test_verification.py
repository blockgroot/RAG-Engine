"""Answer-verification tests (Phase 10, ``app/verification/``).

Uses the REAL embedding provider (BGE-M3, session-scoped fixture — no DB or LLM
needed for this module) to prove the deterministic, no-LLM-call faithfulness
check does what it must:

- a sentence directly present in the evidence verifies as supported;
- a fabricated sentence about an absent, unrelated fact is flagged unsupported;
- a fully-supported multi-sentence answer verifies with an empty unsupported list;
- markdown formatting (headings, bullets, citation markers) doesn't itself break
  sentence-splitting or get mistaken for unverifiable "claims";
- empty answer / empty evidence are handled without raising.
"""

from __future__ import annotations

from app.verification.embedding_similarity import EmbeddingSimilarityVerifier

EVIDENCE = [
    "Full-time employees who have completed probation are eligible for the "
    "Health Allowance, capped at Rs 5,000 per calendar year.",
    "Permissible expenses include fitness equipment, wellness services, and "
    "other health-related products such as ergonomic chairs.",
]


def test_supported_sentence_verifies_as_supported(embedder):
    verifier = EmbeddingSimilarityVerifier(embedder, similarity_threshold=0.65)

    result = verifier.verify(
        "The health allowance is capped at Rs 5,000 per calendar year for eligible employees.",
        EVIDENCE,
    )

    assert result.supported
    assert result.unsupported == []


def test_fabricated_sentence_is_flagged_unsupported(embedder):
    verifier = EmbeddingSimilarityVerifier(embedder, similarity_threshold=0.65)

    result = verifier.verify(
        "The company also provides a fully-paid annual trip to Antarctica for every employee.",
        EVIDENCE,
    )

    assert not result.supported
    assert result.unsupported
    assert result.verdicts[0].best_score < 0.65


def test_mixed_answer_flags_only_the_unsupported_sentence(embedder):
    verifier = EmbeddingSimilarityVerifier(embedder, similarity_threshold=0.65)
    answer = (
        "The health allowance is capped at Rs 5,000 per calendar year. "
        "It also includes free business class flights to the moon for staff."
    )

    result = verifier.verify(answer, EVIDENCE)

    assert not result.supported
    assert any("moon" in s for s in result.unsupported)
    assert not any("5,000" in s for s in result.unsupported)


def test_markdown_formatting_does_not_break_claim_splitting(embedder):
    verifier = EmbeddingSimilarityVerifier(embedder, similarity_threshold=0.65)
    answer = (
        "## Health Allowance\n\n"
        "- The allowance is capped at Rs 5,000 per calendar year. [1]\n"
        "- Permissible expenses include fitness equipment and wellness services. [2]\n"
    )

    result = verifier.verify(answer, EVIDENCE)

    assert result.supported, result.unsupported


def test_empty_answer_and_empty_evidence_do_not_raise(embedder):
    verifier = EmbeddingSimilarityVerifier(embedder)

    assert verifier.verify("", EVIDENCE).supported
    assert verifier.verify("Some short answer text here.", []).supported
