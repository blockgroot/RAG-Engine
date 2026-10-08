"""Inline citations: the model's ``[n]`` markers, checked and mapped to documents.

The grounded prompt already numbers every CONTEXT block ``[1]``, ``[2]``...
The model may put those numbers after a sentence; this module is the only
place they become citations, and it trusts nothing the model wrote:

- a number counts only if a document sat at that position in this prompt.
  A live read of an indexed document is that document. An invented number,
  an attached file, or the graph facts is dropped (those have no document
  to open);
- the link comes from ``documents.source_uri`` on the hit, never from the
  answer text, so a citation cannot be steered to an attacker's URL the way a
  model-written link can (the same reason ``security/links.py`` exists);
- numbers are renumbered by first appearance, one per DOCUMENT, so two chunks
  of one page read as one source and the reader sees [1], [2], [3] in order.

Everything that judges the answer as a claim (the audit, moderation) gets the
text with the markers stripped: ``[2]`` is machinery, not a statement.
"""

from __future__ import annotations

import re
from typing import Any

# ``[3]``, ``[3, 5]``; never a markdown link ``[3](...)`` or a reference
# definition ``[3]: ...`` -- those are links, which the link rule owns.
_MARKER = re.compile(r"[ \t]*\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\](?![(:])")


def strip_citations(text: str) -> str:
    """The answer with every ``[n]`` marker removed."""
    return _MARKER.sub("", text or "")


def link_citations(answer: str, blocks: list[Any]) -> tuple[str, list[dict]]:
    """Renumber valid markers and return ``(answer, cited)``.

    ``blocks[i]`` is the retrieved chunk behind CONTEXT block ``[i + 1]``, or
    ``None`` for a block that is not a document (attachment, graph facts).
    A live read is citable: it carries the indexed document's id, title and
    ``source_uri``. ``cited`` is ``[{n, document_id, title, provider, url}]`` in
    reading order.
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
    # "[3][4]" can be two passages of ONE document: both renumber to [3], and
    # the reader saw "3 3". Within a run of adjacent markers, each number once.
    return _RUN.sub(_dedupe_run, linked), cited


_RUN = re.compile(r"(?:[ \t]*\[\d{1,3}\])+")


def _dedupe_run(match: re.Match) -> str:
    seen: list[str] = []
    for n in re.findall(r"\[(\d{1,3})\]", match.group(0)):
        if n not in seen:
            seen.append(n)
    lead = " " if match.group(0)[:1] in (" ", "\t") else ""
    return lead + "".join(f"[{n}]" for n in seen)
