"""Outbound requests may only carry words the asker typed.

The model writes the web query from input that can hold private facts or
an injection from an earlier turn. Keeping only words from the user's own
questions means nothing leaves that they did not type themselves.
"""

from __future__ import annotations

import re

from ..ingestion.keywords import _STOPWORDS

_WORD = re.compile(r"\w+", re.UNICODE)


def _content(words: list[str]) -> list[str]:
    return [w for w in words if len(w) > 2 and w.lower() not in _STOPWORDS]


def user_worded_query(query: str, user_texts: list[str]) -> str | None:
    """``query`` cut to the words the user typed, or ``None`` to skip the
    search when over half its meaningful words were not theirs (it would
    search for something else)."""
    allowed = {w.lower() for text in user_texts for w in _WORD.findall(text or "")}
    words = _WORD.findall(query or "")
    kept = [w for w in words if w.lower() in allowed]
    meaningful, kept_meaningful = _content(words), _content(kept)
    if not kept_meaningful or len(kept_meaningful) * 2 < len(meaningful):
        return None
    return " ".join(kept)
