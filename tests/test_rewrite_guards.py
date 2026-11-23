"""The follow-up rewrite must not put an earlier ANSWER's words in the asker's mouth.

The rewrite becomes the trusted QUESTION of the grounded prompt. A figure the
asker never typed came from an earlier answer, which may be wrong ("Rs. 15,000"
for a Rs. 5,000 allowance): the rewrite is redone from the asker's questions only.
"""

from __future__ import annotations

from app.config.settings import MemorySettings, RagSettings, RecoverySettings, ReuseSettings
from app.memory.base import ConversationContext, Turn
from app.rag import prompts
from app.rag.pipeline import RagPipeline, _numbers

from .fakes import InMemoryConversationStore, KeywordEmbedder, RecordingLLM, RecordingVectorStore


class _Rewrites(RecordingLLM):
    def __init__(self, replies):
        super().__init__()
        self.replies, self.rewrite_prompts = list(replies), []

    def generate(self, prompt, *a, **k):
        if "STANDALONE QUESTION:" in prompt:
            self.rewrite_prompts.append(prompt)
            return self.replies.pop(0)
        return super().generate(prompt, *a, **k)


def _rewrite(replies, question, turns):
    llm = _Rewrites(replies)
    pipe = RagPipeline(
        llm=llm, embedder=KeywordEmbedder(), store=RecordingVectorStore("o"),
        settings=RagSettings(top_k=5, similarity_threshold=0.35, fallback_response="idk"),
        memory=InMemoryConversationStore(), web_search=None,
        memory_settings=MemorySettings(recent_turns=3), retriever=None,
        reuse_settings=ReuseSettings(enabled=False), recovery_settings=RecoverySettings(enabled=False),
    )
    context = ConversationContext(summary=None, recent_turns=[
        Turn(turn_index=i, question=q, answer=a) for i, (q, a) in enumerate(turns)])
    return pipe._rewrite_question(question, context), llm


WRONG = [("What is the health allowance?", "The health allowance is Rs. 15,000 per year.")]


def test_numbers_ignore_separators():
    assert _numbers("Rs. 15,000 and 5000 in 2026") == {"15000", "5000", "2026"}


def test_a_rewrite_without_a_copied_figure_is_kept_in_one_call():
    got, llm = _rewrite(["How much is the health allowance?"], "how much was it again?", WRONG)
    assert got == "How much is the health allowance?" and len(llm.rewrite_prompts) == 1


def test_a_figure_from_an_earlier_answer_is_rewritten_from_questions_only():
    got, llm = _rewrite(
        ["What can I spend the Rs. 15,000 health allowance on?",
         "What can I spend the health allowance on?"],
        "what can I spend that allowance on?", WRONG)
    assert got == "What can I spend the health allowance on?"
    retry = llm.rewrite_prompts[1]
    assert "Rs. 15,000 per year" not in retry and "What is the health allowance?" in retry


def test_a_figure_the_asker_typed_is_kept():
    got, _ = _rewrite(["Is the Rs. 15,000 limit for the health allowance?"],
                      "is the 15,000 limit for that?", WRONG)
    assert got == "Is the Rs. 15,000 limit for the health allowance?"


def test_a_retry_that_still_copies_a_figure_falls_back_to_the_question():
    got, _ = _rewrite(["Is it Rs. 15,000?", "Is it Rs. 15,000 a year?"], "how much is it?", WRONG)
    assert got == "how much is it?"


def test_the_prompt_forbids_copying_claims_and_attaching_old_topics():
    prompt = prompts.build_rewrite_prompt("what is the referral process?", None, WRONG)
    assert "never copy a figure" in prompt and "starts a new topic" in prompt


def test_the_prompt_forbids_adding_details_from_an_earlier_answer():
    """Staging: 'who has to approve it?' after a WFH answer naming Keka became
    '...request in Keka?', and search returned only the Keka section, never the
    one naming the approvers (Team Lead and HR)."""
    prompt = prompts.build_rewrite_prompt("who has to approve it?", None, [
        ("How do I request work from home?", 'Submit a "Work From Home" request in Keka.')])
    assert "FEWEST words" in prompt and "Never add a detail the user did not ask about" in prompt
