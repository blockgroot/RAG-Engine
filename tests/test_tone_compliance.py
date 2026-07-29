"""Grounding Gap follow-up: Mode A/B tone-compliance guard.

Deterministic unit tests with fakes — no DB, real LLM, or embedding model
(same convention as test_recovery.py). Proves the pipeline (1) parses the
``MODE: A|B|C`` tag the prompt now requires, (2) detects Mode A or B answers
that still use forbidden meta-language despite the tag — including "doc"/
"docs" shorthand, not just "document"/"documents" — (3) retries exactly once
with a corrective reminder, (4) degrades gracefully — never loops, never
fails the request — if the retry still violates or the model omits the tag
entirely (e.g. any pre-existing fake/test that returns untagged plain text).
"""

from __future__ import annotations

from app.config.settings import RagSettings, RecoverySettings, ReuseSettings
from app.rag.pipeline import RagPipeline, _parse_tagged_mode, _violates_mode_b_tone
from app.rag.prompts import MODE_B_FORBIDDEN_PHRASES

from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

ORG = "org-tone"
FALLBACK = "I don't have information on that in the available policy documents."


def _pipeline(llm: RecordingLLM, store: TopicAwareVectorStore) -> RagPipeline:
    return RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(top_k=3, similarity_threshold=0.35, fallback_response=FALLBACK),
        memory=None,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
    )


VIOLATING_MODE_B = (
    "MODE: B\n\n"
    "The documents do not explicitly answer this question. I cannot give a "
    "definitive answer from the available documents."
)
COMPLIANT_MODE_B = (
    "MODE: B\n\n"
    "You have access to 25 days of paid annual leave [1]. For parental leave "
    "specifics, your HR team can help."
)


# -- _parse_tagged_mode -------------------------------------------------------


def test_parse_tagged_mode_extracts_mode_and_text():
    mode, text = _parse_tagged_mode("MODE: B\n\nHello there.")
    assert mode == "B"
    assert text == "Hello there."


def test_parse_tagged_mode_is_case_insensitive_and_tolerant_of_whitespace():
    mode, text = _parse_tagged_mode("  mode: a  \n\n\n  25 days. [1]")
    assert mode == "A"
    assert text == "25 days. [1]"


def test_parse_tagged_mode_returns_none_for_untagged_text():
    mode, text = _parse_tagged_mode("25 days. [1]")
    assert mode is None
    assert text == "25 days. [1]"


# -- _violates_mode_b_tone -----------------------------------------------------


def test_violates_mode_b_tone_catches_each_forbidden_phrase():
    for phrase in MODE_B_FORBIDDEN_PHRASES:
        assert _violates_mode_b_tone(f"Some text with {phrase} in it.")


def test_violates_mode_b_tone_false_for_natural_language():
    assert not _violates_mode_b_tone(
        "You have access to 25 days of paid annual leave [1]. "
        "For parental leave specifics, your HR team can help."
    )


def test_violates_mode_b_tone_catches_doc_shorthand_not_just_document():
    """A live query showed the model routing around the ban by shortening
    'document(s)' to 'doc(s)' once the longer form was forbidden."""
    assert _violates_mode_b_tone("The doc mentions employees get 25 days.")
    assert _violates_mode_b_tone("The docs do not contain that information.")
    assert _violates_mode_b_tone("According to the doc, leave is 25 days.")


# -- pipeline wiring: bounded retry --------------------------------------------


def test_generate_retries_once_when_mode_b_violates_tone():
    llm = RecordingLLM(answers=[VIOLATING_MODE_B, COMPLIANT_MODE_B])
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("What is the parental leave policy?", ORG)

    assert llm.grounded_calls == 2  # exactly one retry
    assert result.tone_retry_used is True
    assert result.response_mode == "B"
    assert "does not explicitly answer" not in result.answer.lower()
    assert "cannot give a definitive answer" not in result.answer.lower()
    assert result.answer == (
        "You have access to 25 days of paid annual leave [1]. "
        "For parental leave specifics, your HR team can help."
    )
    assert result.answered is True


def test_generate_does_not_retry_when_mode_b_is_already_compliant():
    llm = RecordingLLM(answer=COMPLIANT_MODE_B)
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("What is the parental leave policy?", ORG)

    assert llm.grounded_calls == 1  # no retry needed
    assert result.tone_retry_used is False
    assert result.response_mode == "B"


def test_generate_gracefully_degrades_if_retry_still_violates():
    """At most one retry — never loop, never fail the request."""
    llm = RecordingLLM(answers=[VIOLATING_MODE_B, VIOLATING_MODE_B])
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("What is the parental leave policy?", ORG)

    assert llm.grounded_calls == 2  # exactly one retry, not more
    assert result.tone_retry_used is True
    # Still accepted (graceful degradation), not turned into a hard failure.
    assert result.answered is True


def test_generate_retries_mode_a_when_it_contains_forbidden_phrase():
    """The guard also covers Mode A now — a fully-supported answer narrating
    "the document says X" is just as robotic as Mode B doing it."""
    llm = RecordingLLM(
        answers=[
            "MODE: A\n\nThe document says employees get 25 days. [1]",
            "MODE: A\n\nYou get 25 days of paid annual leave. [1]",
        ]
    )
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("How many annual leave days do I get?", ORG)

    assert llm.grounded_calls == 2  # exactly one retry
    assert result.tone_retry_used is True
    assert result.response_mode == "A"
    assert "the document says" not in result.answer.lower()
    assert result.answer == "You get 25 days of paid annual leave. [1]"


def test_generate_does_not_retry_mode_a_when_already_compliant():
    llm = RecordingLLM(answer="MODE: A\n\nYou get 25 days of paid annual leave. [1]")
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("How many annual leave days do I get?", ORG)

    assert llm.grounded_calls == 1
    assert result.tone_retry_used is False
    assert result.response_mode == "A"


def test_untagged_answer_degrades_gracefully_no_tone_check():
    """Pre-existing fakes/tests that return plain untagged text must keep working."""
    llm = RecordingLLM(answer="25 days. [1]")
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("How many annual leave days do I get?", ORG)

    assert llm.grounded_calls == 1  # no retry — mode is unknown, not "B"
    assert result.response_mode is None
    assert result.tone_retry_used is False
    assert result.answer == "25 days. [1]"


def test_mode_c_tag_still_detected_as_fallback():
    """Gate passes (a chunk clears the threshold) but the model still chooses
    Mode C for this question. Note: a refusal re-derives its final RagResult
    via ``_gate_failed`` (which may also consider web-search), not via
    ``replace()`` on ``_generate``'s result, so ``response_mode`` isn't
    preserved on the final refusal result — only the answer/answered contract
    is guaranteed here; ``response_mode`` is a best-effort diagnostic only on
    a non-refusal answer (see the Mode A/B tests above)."""
    llm = RecordingLLM(answer=f"MODE: C\n\n{FALLBACK}")
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("How many annual leave days do I get?", ORG)

    assert result.answered is False
    assert result.answer == FALLBACK
    assert llm.grounded_calls == 1  # no tone retry for mode C
