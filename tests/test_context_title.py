"""Chunk context includes its source document title (bug found live).

A question naming its source by title (e.g. "what does 'X' cover?") had
nothing to match against when the chunk's own body text never repeated that
title — observed on a real Drive doc whose title never appeared in its own
content, causing every question about it to hit the fixed fallback despite
good retrieval scores. Prefixing "(From: <title>)" onto each chunk before
it enters the grounded prompt fixes this without inventing any fact — it's
still literally what's in CONTEXT.
"""

from __future__ import annotations

from app.config.settings import RagSettings, RecoverySettings, ReuseSettings
from app.rag.pipeline import RagPipeline
from app.vectorstore.base import RetrievedChunk, VectorStore
from .fakes import KeywordEmbedder, RecordingLLM

ORG = "org-title"
FALLBACK = "I don't have information on that in the available policy documents."


class _TitledStore(VectorStore):
    def __init__(self, title: str, content: str) -> None:
        self._title = title
        self._content = content

    def create_organization(self, name: str) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def add_document(self, *a, **kw) -> str:  # pragma: no cover - unused
        raise NotImplementedError

    def query(self, org_id, query_embedding, top_k=5, **kwargs):
        return [
            RetrievedChunk(
                content=self._content,
                score=0.9,
                document_id="doc-1",
                chunk_index=0,
                org_id=org_id,
                document_title=self._title,
            )
        ]


def _pipeline(llm: RecordingLLM, store: VectorStore) -> RagPipeline:
    return RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=store,
        settings=RagSettings(top_k=3, similarity_threshold=0.1, fallback_response=FALLBACK),
        memory=None,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
    )


def test_grounded_prompt_includes_the_source_document_title():
    llm = RecordingLLM(answer="MODE: A\n\nThe document covers AI frameworks.")
    store = _TitledStore("AI Development Ecosystem", "This chunk introduces AI frameworks.")
    pipe = _pipeline(llm, store)

    pipe.answer("What does it cover?", org_id=ORG)

    grounded_prompts = [p for p in llm.prompts if "CONTEXT:" in p]
    assert grounded_prompts, "expected at least one grounded-generation prompt"
    assert "(From: AI Development Ecosystem)" in grounded_prompts[-1]


def test_missing_title_does_not_add_a_prefix():
    """A chunk with no document_title (e.g. a plain ingest) must be
    byte-identical to before — no 'From: None' artifact."""
    llm = RecordingLLM(answer="MODE: A\n\nAnswer.")
    store = _TitledStore(None, "Full-time employees get 25 days of leave.")
    pipe = _pipeline(llm, store)

    pipe.answer("How many leave days?", org_id=ORG)

    grounded_prompts = [p for p in llm.prompts if "CONTEXT:" in p]
    assert grounded_prompts
    assert "From: None" not in grounded_prompts[-1]
    assert "(From:" not in grounded_prompts[-1]
