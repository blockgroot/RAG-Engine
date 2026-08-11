"""Tone: MODE meta-language guard + semantic classify + composed empathy.

Deterministic fakes — no DB / real LLM. Empathy is composed in code after
grounding when the aux classifier returns SUPPORTIVE — not via keyword lists
or a bloated grounded prompt.
"""

from __future__ import annotations

from app.config.settings import RagSettings, RecoverySettings, ReuseSettings, ToneSettings
from app.rag.pipeline import RagPipeline, _parse_tagged_mode, _violates_mode_b_tone
from app.rag.prompts import build_grounded_prompt
from app.rag.question_tone import (
    compose_supportive_answer,
    normalize_opener,
    parse_question_tone,
)

from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

ORG = "org-tone"
FALLBACK = "I don't have information on that in the available policy documents."


def _pipeline(
    llm: RecordingLLM,
    store: TopicAwareVectorStore,
    *,
    tone_enabled: bool = True,
) -> RagPipeline:
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
        tone_settings=ToneSettings(enabled=tone_enabled),
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
FACTUAL_COUNSELLING = (
    "MODE: A\n\n"
    "You have a few counselling options available:\n"
    "- Fill out the Counselling Support Google Form\n"
    "- HR will connect you with a counsellor"
)
FACTUAL_MENTAL_HEALTH = (
    "MODE: A\n\n"
    "Under the mental health policy you have access to:\n"
    "- Two paid mental-health days per calendar year\n"
    "- Professional counselling"
)


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


def test_violates_mode_b_tone_catches_source_narration_patterns():
    """Structural detector — not a phrase laundry list."""
    assert _violates_mode_b_tone("The document says employees get 25 days.")
    assert _violates_mode_b_tone("The doc mentions employees get 25 days.")
    assert _violates_mode_b_tone("The docs do not contain that information.")
    assert _violates_mode_b_tone("According to the doc, leave is 25 days.")
    assert _violates_mode_b_tone(
        "I cannot give a definitive answer from the available documents."
    )
    assert _violates_mode_b_tone("The documents do not explicitly answer this.")


def test_violates_mode_b_tone_false_for_natural_language():
    assert not _violates_mode_b_tone(
        "You have access to 25 days of paid annual leave [1]. "
        "For parental leave specifics, your HR team can help."
    )


def test_parse_question_tone_and_compose_helpers():
    assert parse_question_tone("SUPPORTIVE") == "supportive"
    assert parse_question_tone("LABEL: FACTUAL") == "factual"
    assert normalize_opener("OPENER: That sounds really hard.\n") == "That sounds really hard."
    assert compose_supportive_answer("Warm line.", "Policy facts.") == (
        "Warm line.\n\nPolicy facts."
    )


def test_grounded_prompt_has_no_required_tone_block():
    prompt = build_grounded_prompt("q", ["chunk"], FALLBACK)
    assert "REQUIRED_TONE" not in prompt
    assert "I'm sorry you've been feeling" not in prompt


def test_generate_retries_once_when_mode_b_violates_tone():
    llm = RecordingLLM(answers=[VIOLATING_MODE_B, COMPLIANT_MODE_B])
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("What is the parental leave policy?", ORG)

    assert llm.tone_classify_calls == 1
    assert llm.grounded_calls == 2
    assert result.tone_retry_used is True
    assert result.response_mode == "B"
    assert result.question_tone == "factual"
    assert llm.empathy_opener_calls == 0


def test_generate_does_not_retry_when_mode_b_is_already_compliant():
    llm = RecordingLLM(answer=COMPLIANT_MODE_B)
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("What is the parental leave policy?", ORG)

    assert llm.grounded_calls == 1
    assert result.tone_retry_used is False
    assert llm.empathy_opener_calls == 0


def test_generate_gracefully_degrades_if_retry_still_violates():
    llm = RecordingLLM(answers=[VIOLATING_MODE_B, VIOLATING_MODE_B])
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("What is the parental leave policy?", ORG)

    assert llm.grounded_calls == 2
    assert result.tone_retry_used is True
    assert result.answered is True


def test_generate_retries_mode_a_when_it_contains_forbidden_phrase():
    llm = RecordingLLM(
        answers=[
            "MODE: A\n\nThe document says employees get 25 days. [1]",
            "MODE: A\n\nYou get 25 days of paid annual leave. [1]",
        ]
    )
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("How many annual leave days do I get?", ORG)

    assert llm.grounded_calls == 2
    assert result.tone_retry_used is True
    assert "the document says" not in result.answer.lower()


def test_untagged_answer_degrades_gracefully_no_tone_check():
    llm = RecordingLLM(answer="25 days. [1]")
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("How many annual leave days do I get?", ORG)

    assert llm.grounded_calls == 1
    assert result.response_mode is None


def test_mode_c_tag_still_detected_as_fallback():
    llm = RecordingLLM(answer=f"MODE: C\n\n{FALLBACK}")
    store = TopicAwareVectorStore(ORG, [("doc-1", "leave: 25 days annual")])
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("How many annual leave days do I get?", ORG)

    assert result.answered is False
    assert llm.empathy_opener_calls == 0


def test_supportive_composes_opener_onto_grounded_facts():
    """Paraphrased distress → SUPPORTIVE classify → opener prepended in code."""
    llm = RecordingLLM(
        answer=FACTUAL_COUNSELLING,
        question_tone="SUPPORTIVE",
        empathy_opener="That sounds really hard — I'm glad you reached out.",
    )
    store = TopicAwareVectorStore(
        ORG,
        [("doc-1", "counselling: fill Counselling Support form; HR connects you")],
    )
    pipeline = _pipeline(llm, store)

    result = pipeline.answer(
        "Work has been crushing me lately and I don't know how to cope — any help?",
        ORG,
    )

    assert result.question_tone == "supportive"
    assert llm.tone_classify_calls == 1
    assert llm.empathy_opener_calls == 1
    assert llm.grounded_calls == 1
    assert result.answer.startswith("That sounds really hard")
    assert "Counselling Support" in result.answer
    assert result.tone_retry_used is False


def test_factual_mental_health_ask_gets_no_opener():
    llm = RecordingLLM(answer=FACTUAL_MENTAL_HEALTH, question_tone="FACTUAL")
    store = TopicAwareVectorStore(
        ORG,
        [("doc-1", "mental health policy: two paid MH days; counselling; five sessions")],
    )
    pipeline = _pipeline(llm, store)

    result = pipeline.answer("What should I know from 'Mental Health Policy'?", ORG)

    assert result.question_tone == "factual"
    assert llm.empathy_opener_calls == 0
    assert result.answer.startswith("Under the mental health policy")
    assert "sorry" not in result.answer.lower()


def test_tone_disabled_skips_classify_and_opener():
    llm = RecordingLLM(answer=FACTUAL_COUNSELLING, question_tone="SUPPORTIVE")
    store = TopicAwareVectorStore(
        ORG,
        [("doc-1", "counselling: fill Counselling Support form; HR connects you")],
    )
    pipeline = _pipeline(llm, store, tone_enabled=False)

    result = pipeline.answer("I am feeling very stressed lately what should i do?", ORG)

    assert llm.tone_classify_calls == 0
    assert llm.empathy_opener_calls == 0
    assert result.question_tone is None
    assert result.answer.startswith("You have a few counselling")


def test_followup_uses_original_user_question_for_empathy():
    """Conversation rewrite is retrieval-oriented; tone must use the raw turn.

    After a Mental Health Policy ask, rewrite may become a factual-looking
    standalone question — classify/opener must still see the user's distress.
    """
    llm = RecordingLLM(
        answer=FACTUAL_COUNSELLING,
        question_tone="SUPPORTIVE",
        empathy_opener="I'm sorry you're feeling so stressed — that sounds really hard.",
    )
    store = TopicAwareVectorStore(
        ORG,
        [("doc-1", "counselling: fill Counselling Support form; HR connects you")],
    )
    pipeline = _pipeline(llm, store)

    # Simulate _generate after rewrite: retrieval question looks factual,
    # user_question is the real follow-up.
    hits = store.query(ORG, KeywordEmbedder().embed(["counselling"])[0], top_k=3)
    result = pipeline._generate(
        "What mental health counselling and leave options does the company offer?",
        hits,
        hits[0].score if hits else 0.9,
        retrieval_reused=False,
        user_question="I am feeling very stressed lately what should i do?",
    )

    assert result.question_tone == "supportive"
    assert llm.empathy_opener_calls == 1
    assert result.answer.startswith("I'm sorry you're feeling so stressed")
    assert "Counselling Support" in result.answer
