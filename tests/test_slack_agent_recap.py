"""SlackAgent's recap retry: recency-selected evidence for "catch me up" questions.

Every other retrieval path here ranks chunks by similarity to the question,
which is the wrong selection for a recap request — nothing resembles "catch me
up on the last few days", so the ordinary path retrieves arbitrary chunks and
correctly refuses. This proves the recap retry only fires on that refusal, and
only ever turns a refusal into a grounded answer, never the reverse.
"""

from __future__ import annotations

from app.agent.slack_agent import SlackAgent
from app.rag.pipeline import RagPipeline
from app.vectorstore.base import RetrievedChunk, VectorStore

from .fakes import KeywordEmbedder, RecordingLLM

_ORG = "org-slack-recap"


class _EmptyQueryStore(VectorStore):
    """``query`` finds nothing (as a real similarity search would for a recap
    question); ``recent_chunks`` returns real content, like the live corpus."""

    def __init__(self, recap_chunks: list[RetrievedChunk] | None = None) -> None:
        self._recap_chunks = recap_chunks if recap_chunks is not None else []

    def create_organization(self, name: str) -> str:  # pragma: no cover - unused
        return _ORG

    def add_document(self, *args, **kwargs) -> str:  # pragma: no cover - unused
        return "doc-unused"

    def query(self, org_id, query_embedding, top_k=5, workspace_id=None, source_provider=None):
        return []

    def recent_chunks(self, org_id, provider, *, workspace_id=None, limit=40):
        return self._recap_chunks


def _pipeline(store: VectorStore, llm: RecordingLLM) -> RagPipeline:
    return RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        memory=None,
        web_search=None,
        source_provider="slack",
    )


def test_recap_fires_only_after_the_ordinary_path_refuses():
    chunks = [
        RetrievedChunk(
            content="Standup: shipped the export feature today.",
            score=0.0,
            document_id="d1",
            chunk_index=0,
            org_id=_ORG,
            document_title="#eng-standup: Standup notes",
        )
    ]
    store = _EmptyQueryStore(recap_chunks=chunks)
    llm = RecordingLLM(answer="Recent activity: the export feature shipped.")
    agent = SlackAgent(_pipeline(store, llm))

    response = agent.answer("Catch me up on the last few days", _ORG)

    assert response.grounded is True
    assert response.source == "slack"
    assert "export feature" in response.answer
    assert len(response.citations) == 1
    # One recap prompt must have gone to the LLM in addition to the ordinary one.
    assert any("RECENT THREADS" in p and "#eng-standup" in p for p in llm.prompts), (
        "the channel label must reach the prompt, or a channel-named question "
        "has no way to be confirmed and gets refused"
    )


def test_recap_never_overrides_a_grounded_ordinary_answer():
    """If the similarity path already answered, recap must not run at all."""

    class _HitStore(_EmptyQueryStore):
        def query(self, org_id, query_embedding, top_k=5, workspace_id=None, source_provider=None):
            return [
                RetrievedChunk(
                    content="Parental leave is 18 weeks.",
                    score=0.9,
                    document_id="d2",
                    chunk_index=0,
                    org_id=_ORG,
                )
            ]

    store = _HitStore(recap_chunks=[])
    llm = RecordingLLM(answer="Parental leave is 18 weeks. [1]")
    agent = SlackAgent(_pipeline(store, llm))

    response = agent.answer("What is parental leave?", _ORG)

    assert response.grounded is True
    assert not any("RECENT THREADS" in p for p in llm.prompts), (
        "recap must never run when the ordinary path already grounded"
    )


def test_recap_falls_back_to_the_original_refusal_when_it_finds_nothing_either():
    store = _EmptyQueryStore(recap_chunks=[])  # nothing stored at all
    llm = RecordingLLM()
    agent = SlackAgent(_pipeline(store, llm))

    response = agent.answer("Catch me up", _ORG)

    assert response.grounded is False
    assert response.source == "none"


def test_recap_respects_the_fallback_when_the_model_still_declines():
    """Recency-selected chunks may still not address the question -- the
    prompt must be free to refuse, and the caller must accept that refusal."""
    chunks = [
        RetrievedChunk(
            content="Unrelated chatter.",
            score=0.0,
            document_id="d3",
            chunk_index=0,
            org_id=_ORG,
        )
    ]
    store = _EmptyQueryStore(recap_chunks=chunks)
    pipeline = _pipeline(store, RecordingLLM())
    llm = RecordingLLM(answer=pipeline.fallback_response)
    agent = SlackAgent(_pipeline(store, llm))

    response = agent.answer("Catch me up", _ORG)

    assert response.grounded is False


def test_a_store_without_recency_support_leaves_recap_a_no_op():
    """Docs/GitHub-style stores that never implemented ``recent_chunks`` must
    not break the Slack agent -- they simply never get a recap retry."""

    class _NoRecapStore(VectorStore):
        def create_organization(self, name: str) -> str:  # pragma: no cover
            return _ORG

        def add_document(self, *args, **kwargs) -> str:  # pragma: no cover
            return "doc-unused"

        def query(self, org_id, query_embedding, top_k=5, workspace_id=None, source_provider=None):
            return []

    store = _NoRecapStore()
    llm = RecordingLLM()
    agent = SlackAgent(_pipeline(store, llm))

    response = agent.answer("Catch me up", _ORG)

    assert response.grounded is False
    assert not any("RECENT THREADS" in p for p in llm.prompts)


def test_channel_is_parsed_back_out_of_the_stored_document_title():
    from app.agent.slack_agent import _channel_of

    assert _channel_of("#hand-book-testing: EOD update...") == "hand-book-testing"
    assert _channel_of("Thread in #handbook") == "handbook"
    assert _channel_of(None) is None
    assert _channel_of("some prose with no channel prefix") is None


def test_recap_labels_channel_from_external_id_when_title_has_no_prefix(monkeypatch):
    """Threads ingested before titles carried ``#channel:`` still recap."""
    chunks = [
        RetrievedChunk(
            content="Standup: shipped the export feature today.",
            score=0.0,
            document_id="d1",
            chunk_index=0,
            org_id=_ORG,
            document_title="What is the leave policy?",
            source_external_id="C99:100.0",
        )
    ]
    monkeypatch.setattr(
        "app.agent.slack_agent._channel_names_for",
        lambda org_id, workspace_id: {"C99": "handbook"},
    )
    store = _EmptyQueryStore(recap_chunks=chunks)
    llm = RecordingLLM(answer="Recent activity: the export feature shipped.")
    agent = SlackAgent(_pipeline(store, llm))

    response = agent.answer("What was discussed in #handbook recently?", _ORG)

    assert response.grounded is True
    assert any("(#handbook)" in p for p in llm.prompts)


def test_recap_prompt_labels_each_thread_with_its_channel():
    from app.rag.prompts import build_slack_recap_prompt

    prompt = build_slack_recap_prompt(
        "what happened",
        [("shipped the thing", "eng-standup"), ("no channel here", None)],
        "fallback text",
    )
    assert "(#eng-standup)" in prompt
    assert "shipped the thing" in prompt
    assert "no channel here" in prompt
