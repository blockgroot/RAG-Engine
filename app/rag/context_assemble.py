"""Trim retrieved chunk text to a char budget before the grounded prompt.

Shorter context → fewer prompt tokens → faster LLM generation. We keep chunk
order (most relevant first) and prefer the head of each chunk so lead policy
sentences survive truncation.
"""

from __future__ import annotations


def assemble_context_texts(contents: list[str], max_chars: int) -> list[str]:
    """Return a prefix of ``contents`` whose total length is ≤ ``max_chars``.

    ``max_chars <= 0`` means no budget (return all texts unchanged).
    """
    if max_chars <= 0:
        return list(contents)

    out: list[str] = []
    used = 0
    for text in contents:
        if not text:
            continue
        if used >= max_chars:
            break
        room = max_chars - used
        if len(text) <= room:
            out.append(text)
            used += len(text)
            continue
        # Too little room for a useful fragment — stop rather than add noise.
        if room < 80:
            break
        out.append(text[: room - 1].rstrip() + "…")
        break
    return out
