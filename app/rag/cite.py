"""Inline citations: the model's ``[n]`` markers, checked and mapped to documents.

Trusts nothing the model wrote:

- a number counts only if a document sat at that block (a live read of an
  indexed document is that document; attachments and graph facts are not);
- the link is ``documents.source_uri`` from the hit, never answer text;
- numbers are renumbered by first appearance, one per DOCUMENT.

The audit and moderation judge the text with markers stripped.
"""

from __future__ import annotations

import re
from typing import Any

# ``[3]``, ``[3, 5]``; never ``[3](...)`` or ``[3]: ...`` (links, owned by the
# link rule).
_MARKER = re.compile(r"[ \t]*\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\](?![(:])")


def strip_citations(text: str) -> str:
    """The answer with every ``[n]`` marker removed."""
    return _MARKER.sub("", text or "")


def link_citations(answer: str, blocks: list[Any]) -> tuple[str, list[dict]]:
    """Renumber valid markers and return ``(answer, cited)``.

    ``blocks[i]`` is the chunk behind CONTEXT block ``[i + 1]``, or ``None`` for
    a non-document block. ``cited`` is ``[{n, document_id, title, provider,
    url}]`` in reading order.
    """
    order: dict[str, int] = {}
    cited: list[dict] = []

    def renumber(match: re.Match) -> str:
        out: list[str] = []
        for raw in match.group(1).split(","):
            i = int(raw) - 1
            hit = blocks[i] if 0 <= i < len(blocks) else None
            if hit is None:
                continue
            n = order.get(hit.document_id)
            if n is None:
                n = order[hit.document_id] = len(order) + 1
                url = (getattr(hit, "source_uri", None) or "").strip()
                cited.append({
                    "n": n,
                    "document_id": hit.document_id,
                    "title": (getattr(hit, "document_title", None) or "").strip() or None,
                    "provider": getattr(hit, "source_provider", None),
                    "url": url if url.startswith(("https://", "http://")) else None,
                })
            if f"[{n}]" not in out:
                out.append(f"[{n}]")
        return " " + "".join(out) if out else ""

    linked = _MARKER.sub(renumber, answer or "")
    # Adjacent markers citing one document collapse to one ("3 3" otherwise).
    return _RUN.sub(_dedupe_run, linked), cited


_RUN = re.compile(r"(?:[ \t]*\[\d{1,3}\])+")


def _dedupe_run(match: re.Match) -> str:
    seen: list[str] = []
    for n in re.findall(r"\[(\d{1,3})\]", match.group(0)):
        if n not in seen:
            seen.append(n)
    lead = " " if match.group(0)[:1] in (" ", "\t") else ""
    return lead + "".join(f"[{n}]" for n in seen)
