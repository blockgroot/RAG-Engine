"""Inline citations: a model's [n] counts only when it points at a retrieved document.

The rules under test are the ones a reader relies on: a chip opens the page the
sentence came from, never a number the model invented, never a URL the model
wrote, and never something that is not a document (an attached file).
"""

from __future__ import annotations

from app.config.settings import RagSettings
from app.rag.cite import link_citations, strip_citations
from app.rag.pipeline import RagPipeline
from app.vectorstore.base import RetrievedChunk

from .fakes import KeywordEmbedder, RecordingLLM, TopicAwareVectorStore

FALLBACK = "I don't have information on that in the available policy documents."


def _hit(doc: str, title: str, url: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        content="x", score=0.9, document_id=doc, chunk_index=0, org_id="org",
        document_title=title, source_provider="notion", source_uri=url,
    )


LEAVE = _hit("d-leave", "Leave Policy", "https://notion.so/leave")
FAQ = _hit("d-faq", "HR FAQ", "https://drive.google.com/faq")


def test_numbers_follow_reading_order_and_map_to_documents():
    text, cited = link_citations("25 days [3]. Carry over 5 [1].", [None, LEAVE, FAQ])
    assert text == "25 days [1]. Carry over 5."  # [1] pointed at a non-document block
    assert cited == [{
        "n": 1, "document_id": "d-faq", "title": "HR FAQ",
        "provider": "notion", "url": "https://drive.google.com/faq",
    }]


def test_two_chunks_of_one_document_are_one_source():
    leave_2 = _hit("d-leave", "Leave Policy")
    text, cited = link_citations("A [1]. B [2]. C [1, 3].", [LEAVE, leave_2, FAQ])
    assert text == "A [1]. B [1]. C [1][2]."
    assert [c["document_id"] for c in cited] == ["d-leave", "d-faq"]


def test_an_invented_number_is_dropped_without_leaving_a_gap():
    text, cited = link_citations("Leave is 25 days [9].", [LEAVE])
    assert (text, cited) == ("Leave is 25 days.", [])


def test_the_link_comes_from_the_document_never_a_scheme_we_do_not_open():
    hostile = _hit("d-x", "Doc", "javascript:alert(1)")
    _, cited = link_citations("Fact [1].", [hostile])
    assert cited[0]["url"] is None


def test_markdown_links_and_reference_definitions_are_not_citations():
    text, cited = link_citations("See [1](https://a.b) and\n[1]: https://a.b", [LEAVE])
    assert text == "See [1](https://a.b) and\n[1]: https://a.b"
    assert cited == []


def test_strip_removes_every_marker():
    assert strip_citations("25 days [1][2]. Five [3, 4].") == "25 days. Five."


def _pipeline(answer: str, **kw):
    llm = RecordingLLM(answer=answer)
    return llm, RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=TopicAwareVectorStore("org-1", [("doc-1", "Meals are reimbursable up to 40 dollars per day.")]),
        settings=RagSettings(top_k=3, similarity_threshold=0.35, fallback_response=FALLBACK),
        **kw,
    )


def test_the_pipeline_returns_the_documents_behind_the_markers():
    _, pipeline = _pipeline("MODE: A\n\nMeals are covered up to 40 dollars a day [1].")
    result = pipeline.answer("are meals reimbursable per day?", "org-1")
    assert result.answered
    assert result.answer.endswith("[1].")
    assert [c["document_id"] for c in result.cited] == [result.sources[0].document_id]


def test_a_number_pointing_at_an_attached_file_is_not_a_citation():
    # The file leads the context, so it is [1]; the retrieved policy is [2].
    _, pipeline = _pipeline("MODE: A\n\nThe dinner was 32 dollars [1], under the cap [2].")
    result = pipeline.answer(
        "is this dinner claimable per day?", "org-1",
        attachments=[("receipt.txt", "Dinner, total 32 dollars.", False)],
    )
    assert result.answer.endswith("dollars, under the cap [1].")
    assert len(result.cited) == 1 and result.cited[0]["n"] == 1


def test_a_live_read_cites_the_stored_document_not_a_url_in_the_text():
    live = RetrievedChunk(
        content="x", score=0.9, document_id="d-linear", chunk_index=0, org_id="org",
        document_title="SYV-5", source_provider="linear",
        source_uri="https://linear.app/syvora/issue/SYV-5",
    )
    text, cited = link_citations(
        "SYV-5 is in progress [1]. See https://evil.example.",
        [live],
    )
    assert text == "SYV-5 is in progress [1]. See https://evil.example."
    assert cited == [{
        "n": 1, "document_id": "d-linear", "title": "SYV-5",
        "provider": "linear", "url": "https://linear.app/syvora/issue/SYV-5",
    }]


def test_the_prompt_asks_for_markers():
    llm, pipeline = _pipeline("MODE: A\n\nMeals are covered [1].")
    pipeline.answer("are meals reimbursable per day?", "org-1")
    prompt = next(p for p in llm.prompts if "<<<UNTRUSTED_DOCUMENT_CONTENT>>>" in p)
    assert "add the number of the CONTEXT block" in prompt
    assert "Do not print [n]" not in prompt


def test_a_saved_turn_keeps_the_citation_list():
    """Reopening a chat reads this list back. Losing it is why the sources vanished."""
    from tests.fakes import InMemoryConversationStore

    memory = InMemoryConversationStore()
    cid = memory.create_conversation("org-1")
    _, pipeline = _pipeline(
        "MODE: A\n\nMeals are covered up to 40 dollars a day [1].", memory=memory
    )
    pipeline.answer("are meals reimbursable per day?", "org-1", conversation_id=cid)
    turn = memory.get_turns(cid)[-1]
    assert turn.answer.endswith("[1].")
    assert turn.cited[0]["document_id"] == "doc-1"
    assert turn.cited[0]["n"] == 1


def test_two_passages_of_one_document_cite_it_once():
    """"[3][4]" from one Slack thread read as a superscript "3 3"."""
    thread = _hit("d1", "#rag-updates")
    other = _hit("d2", "Leave Policy")
    text, cited = link_citations("Live reads replace synced copies [1][2]. Leave [3].",
                                 [thread, thread, other])
    assert text == "Live reads replace synced copies [1]. Leave [2]."
    assert [c["document_id"] for c in cited] == ["d1", "d2"]
