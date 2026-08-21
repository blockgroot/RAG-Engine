"""Red-team corpus expansion (stress-testing/red-teaming gap, no LLM/DB needed).

Structural checks only — the actual behavioral proof (does the model resist
these prompts) is ``scripts/probe_injection.py`` against a real LLM, gated in
CI's nightly ragas-tier (see .github/workflows/eval.yml). These tests just
make sure the corpus/case shape itself can't silently rot: a case id that
stops matching the probe's filter would drop out of the CI gate with no
error anywhere.
"""

from __future__ import annotations

import re

from evaluation.golden_set import CORPUS, GOLDEN_CASES

_RED_TEAM_PREFIXES = ("injection-", "bias-")


def _red_team_cases():
    return [c for c in GOLDEN_CASES if c.id.startswith(_RED_TEAM_PREFIXES)]


def test_red_team_corpus_has_both_injection_and_bias_cases():
    ids = {c.id for c in _red_team_cases()}
    assert any(i.startswith("injection-") for i in ids)
    assert any(i.startswith("bias-") for i in ids)
    assert len(ids) >= 5, "the red-team corpus should not silently shrink"


def test_every_red_team_case_has_a_forbidden_pattern():
    """A red-team case with no forbidden_answer_pattern can't actually catch
    a leak — it would only ever check path/fact correctness."""
    for c in _red_team_cases():
        assert c.forbidden_answer_pattern, f"{c.id} has no forbidden_answer_pattern"
        re.compile(c.forbidden_answer_pattern)  # must be valid regex


def test_bias_cases_are_fallback_category_with_no_expected_facts():
    """A bias-probing case has no correct 'fact' to report — the only safe
    answer is the fixed fallback, so it must never carry expected_facts."""
    for c in _red_team_cases():
        if c.id.startswith("bias-"):
            assert c.category == "fallback"
            assert c.expected_facts == []
            assert c.expected_source == "none"


def test_injection_cases_pair_a_real_fact_with_an_adversarial_corpus_doc():
    """Every injection-* case must be answerable (a real fact to defend) and
    its forbidden pattern must not accidentally also match the real fact."""
    for c in _red_team_cases():
        if not c.id.startswith("injection-"):
            continue
        assert c.category == "answerable"
        assert c.expected_facts, f"{c.id} has no real fact to defend"
        pattern = re.compile(c.forbidden_answer_pattern, re.IGNORECASE)
        for fact in c.expected_facts:
            assert not pattern.search(fact), (
                f"{c.id}: forbidden_answer_pattern matches its own expected fact {fact!r}"
            )


def test_new_dan_payroll_corpus_doc_is_present_and_not_the_only_payroll_source():
    """The jailbreak payload must live in the seeded corpus (so the probe
    actually exercises retrieval + generation against it, not a mocked doc)."""
    payroll_docs = [text for _, text in CORPUS if "Payroll" in text]
    assert payroll_docs, "expected a Payroll Processing corpus document"
    assert any("DAN" in text for text in payroll_docs)
    assert any("last business day" in text for text in payroll_docs)
