"""Progressive delivery of an already-decided answer.

Both `RagPipeline.answer_stream` and `GitHubAgent.answer_stream` chunk a
*finished* string rather than streaming raw LLM tokens — for the same reason in
both cases (Phase 13a): whether a given LLM call is the final one is only known
after inspecting its output, so a call's tokens can still be discarded and
replaced by recovery, a tone retry, or the web-search fallback. Streaming them
live would either leak a draft or require buffering anyway.

The chunking itself was written out identically in both places. It lives here so
the two agents cannot drift apart on how text reaches the SSE transport — the
reasoning above is what differs between them and belongs in their docstrings;
the mechanics do not.
"""

from __future__ import annotations

from collections.abc import Iterator

# Small enough that delivery feels progressive, large enough that an SSE stream
# is not one event per few characters.
DEFAULT_CHUNK_CHARS = 40


def chunk_answer(text: str, chunk_chars: int = DEFAULT_CHUNK_CHARS) -> Iterator[str]:
    """Yield ``text`` in fixed-size slices for progressive delivery.

    Guards ``chunk_chars <= 0``, which would otherwise make ``range`` raise (or,
    with a negative step, loop forever) — a caller passing 0 should get the whole
    answer in one piece, not a hung stream.
    """
    if not text:
        return
    if chunk_chars <= 0:
        yield text
        return
    for i in range(0, len(text), chunk_chars):
        yield text[i : i + chunk_chars]
