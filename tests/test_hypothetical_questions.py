"""Ingest-time hypothetical questions (metadata-creation gap #2).

Deterministic unit tests with a fake LLM (no network). Default is OFF and
must be byte-for-byte the existing contextualize behaviour; opting in folds
2-3 example questions into the SAME LLM call (never a second round-trip) and
appends them to the stored chunk text.
"""

from __future__ import annotations

from app.core.exceptions import LLMProviderError
from app.ingestion.contextualize import contextualize_chunk, contextualize_chunks
from app.llm.base import LLMProvider

DOCUMENT = "# Handbook\n\n## Leave\nPart-time employees get 12 days of paid leave."
CHUNK = "Part-time employees get 12 days of paid leave."


class _FakeLLM(LLMProvider):
    def __init__(self, reply: str = "A short situating context.") -> None:
        self.reply = reply
        self.calls = 0

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        self.calls += 1
        return self.reply


class _RaisingLLM(LLMProvider):
    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        raise LLMProviderError("simulated failure")


def test_default_off_matches_plain_contextualize_shape():
    llm = _FakeLLM("A short situating context.")

    out = contextualize_chunk(llm, DOCUMENT, CHUNK)

    assert out == "A short situating context.\n\n" + CHUNK
    assert llm.calls == 1


def test_enabled_folds_context_and_questions_into_one_call():
    llm = _FakeLLM(
        "CONTEXT: Part-time leave entitlement.\n"
        "QUESTIONS:\n"
        "- How many paid leave days do part-time employees get?\n"
        "- What is the part-time leave policy?"
    )

    out = contextualize_chunk(llm, DOCUMENT, CHUNK, hypothetical_questions=True)

    assert llm.calls == 1, "must never cost a second LLM round-trip"
    assert CHUNK in out
    assert "Part-time leave entitlement." in out
    assert "How many paid leave days do part-time employees get?" in out
    assert "What is the part-time leave policy?" in out


def test_enabled_with_unparseable_reply_falls_back_to_plain_context():
    llm = _FakeLLM("just a context sentence, no shape")

    out = contextualize_chunk(llm, DOCUMENT, CHUNK, hypothetical_questions=True)

    assert out == "just a context sentence, no shape\n\n" + CHUNK


def test_enabled_with_no_questions_line_still_returns_context():
    llm = _FakeLLM("CONTEXT: Part-time leave entitlement.\nQUESTIONS:\n")

    out = contextualize_chunk(llm, DOCUMENT, CHUNK, hypothetical_questions=True)

    assert out == "Part-time leave entitlement.\n\n" + CHUNK


def test_llm_failure_falls_back_to_plain_chunk_regardless_of_mode():
    llm = _RaisingLLM()

    out = contextualize_chunk(llm, DOCUMENT, CHUNK, hypothetical_questions=True)

    assert out == CHUNK


def test_contextualize_chunks_threads_the_flag_through_batch_and_parallel_paths():
    llm = _FakeLLM("CONTEXT: ctx\nQUESTIONS:\n- q1?")
    chunks = ["chunk one", "chunk two"]

    serial = contextualize_chunks(llm, DOCUMENT, chunks, hypothetical_questions=True)
    parallel = contextualize_chunks(
        llm, DOCUMENT, chunks, concurrency=2, hypothetical_questions=True
    )

    for out in (serial, parallel):
        assert all("q1?" in o for o in out)
        assert out[0].endswith("chunk one")
        assert out[1].endswith("chunk two")
