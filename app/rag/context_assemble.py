"""Trim retrieved chunk text to a char budget before the grounded prompt,
keeping order (most relevant first) and each chunk's head."""

from __future__ import annotations


#: Human names for the provenance line ("Notion", not "notion").
_PROVIDER_LABEL = {
    "notion": "Notion",
    "google": "Google Drive",
    "slack": "Slack",
    "linear": "Linear",
    "github": "GitHub",
}


def describe_hit(hit) -> str:
    """One provenance line (title, app, editor, date), then the chunk text.

    States only what the source told us: an unknown editor is omitted, not
    "Unknown", which the model would repeat as an answer.
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
