"""Trim retrieved chunk text to a char budget before the grounded prompt.

Shorter context → fewer prompt tokens → faster LLM generation. We keep chunk
order (most relevant first) and prefer the head of each chunk so lead policy
sentences survive truncation.
"""

from __future__ import annotations


#: Human names for the provenance line. A member asked "who wrote this?" about
#: a Notion page, not about a "notion" string.
_PROVIDER_LABEL = {
    "notion": "Notion",
    "google": "Google Drive",
    "slack": "Slack",
    "linear": "Linear",
    "github": "GitHub",
}


def describe_hit(hit) -> str:
    """One provenance line, then the chunk text.

    Retrieval already JOINed the document row, and "who wrote this?", "when was
    it last updated?" and "which app is this from?" are among the most common
    things anyone asks about a document -- yet only the title used to reach the
    model, so every one of those became a refusal against data we already had.

    Only states what the source actually told us. An unknown editor is omitted
    rather than rendered as "Unknown": a placeholder in the context invites the
    model to answer "who wrote this?" with it.
    """
    parts: list[str] = []
    title = (getattr(hit, "document_title", None) or "").strip()
    if title:
        parts.append(title)
    provider = _PROVIDER_LABEL.get(getattr(hit, "source_provider", None) or "")
    if provider:
        parts.append(provider)
    editor = (getattr(hit, "last_editor", None) or "").strip()
    if editor:
        parts.append(f"last edited by {editor}")
    when = getattr(hit, "last_modified", None)
    if when is not None:
        try:
            parts.append(f"updated {when.strftime('%d %b %Y')}")
        except (AttributeError, ValueError):
            pass

    if not parts:
        return hit.content
    return f"(From: {' · '.join(parts)})\n{hit.content}"


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
