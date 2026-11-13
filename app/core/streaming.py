"""Progressive delivery of an already-decided answer.

Agents chunk a finished string, not raw tokens: a call's output may still be
discarded (recovery, tone retry, web fallback), so live tokens would leak a draft.
"""

from __future__ import annotations

from collections.abc import Iterator

# Small enough that delivery feels progressive, large enough that an SSE stream
# is not one event per few characters.
DEFAULT_CHUNK_CHARS = 40


def chunk_answer(text: str, chunk_chars: int = DEFAULT_CHUNK_CHARS) -> Iterator[str]:
    """Yield ``text`` in fixed-size slices; ``chunk_chars <= 0`` yields it whole instead of hanging."""
    if not text:
        return
    if chunk_chars <= 0:
        yield text
        return
    for i in range(0, len(text), chunk_chars):
        yield text[i : i + chunk_chars]
