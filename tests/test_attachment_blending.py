"""An attached file JOINS retrieval; it does not replace it.

The question this exists for is the real one: somebody uploads an expense
receipt and asks "is this claimable?". That is a question about the receipt
AND about the policy in the corpus, and answering it from either alone answers
a different question. It used to be answered from the receipt alone, because
an attachment short-circuited routing entirely -- and since attachments live
on the CONVERSATION, every later question in that chat ("how much leave do I
have left?") was answered from the receipt too.

So each test here asserts on what reached the PROMPT, not on the wording of an
answer: a fake LLM records the prompt, and the prompt is where the defect was.
"""

from __future__ import annotations

import pytest

from app.config.settings import AttachmentSettings, RagSettings
from app.rag.pipeline import RagPipeline
from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

FALLBACK = "I don't have information on that in the available policy documents."

POLICY = "Meals are reimbursable up to 40 dollars per day on approved travel."
RECEIPT = ("filename", "Dinner at Nandos, total 32 dollars, 4 March.", False)


def _pipeline(docs=((("doc-1"), POLICY),)):
    llm = RecordingLLM(answer="MODE: A\nYes, 32 dollars is under the 40 dollar cap. [1]")
    return llm, RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=TopicAwareVectorStore("org-1", list(docs)),
        settings=RagSettings(
            top_k=3, similarity_threshold=0.35, fallback_response=FALLBACK
        ),
    )


def _prompt(llm) -> str:
    """The GROUNDED-answer prompt, identified by its own fence.

    Not "the longest prompt": on a gate miss the recovery prompt can be
    longer, and a test that reads the wrong prompt reports a context problem
    that is really a retrieval one.
    """
    grounded = [p for p in llm.prompts if "<<<UNTRUSTED_DOCUMENT_CONTENT>>>" in p]
    assert grounded, "no grounded prompt was built -- generation never ran"
    return grounded[-1]


def test_the_corpus_and_the_file_reach_one_prompt():
    """The whole point: both, in one answer."""
    llm, pipeline = _pipeline()
    pipeline.answer(
        "is this dinner receipt claimable?",
        "org-1",
        attachments=[("receipt.txt", RECEIPT[1], False)],
    )

    prompt = _prompt(llm)
    assert "Nandos" in prompt, "the attached receipt never reached the prompt"
    assert "40 dollars per day" in prompt, "the policy was not retrieved"


def test_the_file_is_labelled_so_provenance_survives_blending():
    """Mixing sources is only acceptable while each one still names itself."""
    llm, pipeline = _pipeline()
    pipeline.answer(
        "is this claimable?", "org-1", attachments=[("receipt.txt", RECEIPT[1], False)]
    )

    assert "Attached file: receipt.txt" in _prompt(llm)


def test_a_question_with_nothing_to_do_with_the_file_still_uses_the_corpus():
    """The regression that made every later turn answer from the upload."""
    llm, pipeline = _pipeline()
    pipeline.answer(
        "are meals reimbursable on approved travel?",
        "org-1",
        attachments=[("receipt.txt", RECEIPT[1], False)],
    )

    assert "40 dollars per day" in _prompt(llm)


def test_a_file_still_answers_when_the_corpus_has_nothing():
    """A gate miss with a file attached must not be a refusal.

    "What's the total on this invoice?" is answerable from the invoice and has
    no reason to reach the corpus at all.
    """
    llm, pipeline = _pipeline(docs=(("doc-1", "Dogs are not allowed in the office."),))
    result = pipeline.answer(
        "what is the total on this receipt?",
        "org-1",
        attachments=[("receipt.txt", RECEIPT[1], False)],
    )

    assert result.answered, "a file was attached and the answer was still refused"
    assert "Nandos" in _prompt(llm)


def test_no_attachment_changes_nothing():
    """The path without files must be byte-identical to before."""
    llm, pipeline = _pipeline()
    pipeline.answer("are meals reimbursable on approved travel?", "org-1")

    prompt = _prompt(llm)
    assert "40 dollars per day" in prompt
    assert "Attached file" not in prompt


def test_an_empty_attachment_list_is_not_an_attachment():
    llm, pipeline = _pipeline()
    pipeline.answer("are meals reimbursable on approved travel?", "org-1", attachments=[])
    assert "Attached file" not in _prompt(llm)


def test_a_whitespace_only_file_is_dropped_not_blended():
    """An empty context reaches the prompt as 'this document says nothing'."""
    llm, pipeline = _pipeline()
    pipeline.answer(
        "are meals reimbursable on approved travel?",
        "org-1",
        attachments=[("blank.txt", "   \n  ", False)],
    )
    assert "Attached file" not in _prompt(llm)


def test_the_file_leads_the_context():
    """The document under discussion goes first; the corpus supports it."""
    llm, pipeline = _pipeline()
    pipeline.answer(
        "is this claimable?", "org-1", attachments=[("receipt.txt", RECEIPT[1], False)]
    )

    prompt = _prompt(llm)
    assert prompt.index("Nandos") < prompt.index("40 dollars per day")
