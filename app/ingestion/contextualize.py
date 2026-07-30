"""Contextual retrieval (Phase 6, ingest-time).

When a document is split into chunks, each chunk loses the surrounding context
that told you what it was about ("Part-Time" might only appear in a heading two
chunks up). Following Anthropic's "contextual retrieval" idea, we prepend a short
LLM-generated context to each chunk *before* embedding + storing it, so the chunk
carries its own situating context into both the vector embedding and the keyword
index. This runs once per chunk at ingestion — never at query time — so it adds no
query latency.

The stored chunk becomes ``"<context>\\n\\n<original chunk>"``. Best-effort: if the
LLM call fails or returns nothing, we fall back to the original chunk unchanged.
"""

from __future__ import annotations

from ..core.exceptions import LLMProviderError
from ..llm.base import LLMProvider
from ..llm.metering import log_llm_call

# Cap how much of the document we send as context, to bound cost/latency on very
# large documents (a couple of thousand tokens of surrounding context is plenty).
MAX_DOC_CHARS = 8000


def _build_prompt(document_text: str, chunk: str) -> str:
    return (
        "Here is a document:\n<document>\n"
        f"{document_text[:MAX_DOC_CHARS]}\n</document>\n\n"
        "Here is a chunk taken from that document:\n<chunk>\n"
        f"{chunk}\n</chunk>\n\n"
        "Give a short (1-2 sentence) context situating this chunk within the "
        "document — which section/topic it belongs to and what it covers — to "
        "improve search retrieval. Answer ONLY with the context, no preamble."
    )


def contextualize_chunk(
    llm: LLMProvider,
    document_text: str,
    chunk: str,
    *,
    org_id: str | None = None,
) -> str:
    """Prepend a short generated context to a single chunk (best-effort)."""
    try:
        context = llm.generate(_build_prompt(document_text, chunk)).strip()
        log_llm_call("ingest-context", llm, org_id=org_id)
    except LLMProviderError:
        return chunk
    return f"{context}\n\n{chunk}" if context else chunk


def contextualize_chunks(
    llm: LLMProvider,
    document_text: str,
    chunks: list[str],
    *,
    org_id: str | None = None,
) -> list[str]:
    """Contextualize every chunk of one document, in order."""
    return [
        contextualize_chunk(llm, document_text, chunk, org_id=org_id) for chunk in chunks
    ]
