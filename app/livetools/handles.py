"""Opaque handles for mode B (plan D4, D4a).

The model never sees a document id, an issue UUID, a channel id or a file id:
it sees ``[L1]`` beside a title and names that when it wants a live read. The
map lives only in this request, so a handle from another request -- or an
invented one -- resolves to nothing. ``strip_handles`` removes any the model
echoes into an answer, before it is streamed, cached, stored or logged.
"""

from __future__ import annotations

import re

_PREFIX = {"linear": "L", "google": "D", "notion": "N", "slack": "S"}
_HANDLE_RE = re.compile(r"[ \t]?\[(?:L|D|N|S)\d{1,2}\]")


def mint(candidates: list[tuple[str, str, str, str]]) -> dict[str, tuple[str, str, str]]:
    """``{"L1": (document_id, provider, title)}`` for this request's candidates."""
    out: dict[str, tuple[str, str, str]] = {}
    counts: dict[str, int] = {}
    for doc, provider, _external_id, title in (row[:4] for row in candidates):
        letter = _PREFIX.get(provider)
        if letter is None:
            continue
        counts[letter] = counts.get(letter, 0) + 1
        out[f"{letter}{counts[letter]}"] = (doc, provider, title)
    return out


def resolve(handle: str | None, minted: dict[str, tuple[str, str, str]]) -> str | None:
    """The document id behind ``handle``, only if THIS request minted it."""
    key = (handle or "").strip().strip("[]").upper()
    entry = minted.get(key)
    return entry[0] if entry else None


def strip_handles(text: str) -> str:
    return _HANDLE_RE.sub("", text)
