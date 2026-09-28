"""Outbound requests may only carry words the asker typed.

The web-search query is written by the model, and the model's input includes
the memory-rewritten question -- which can carry a fact from an earlier
PRIVATE answer, and which an injection that reached an earlier turn can steer.
A search query is a request to a third party, so a word in it is a word that
left the building. Keeping only words from the user's own questions means
nothing reaches DuckDuckGo that the user did not already type into it
themselves: no leak by accident, none by injection. Stdlib, microseconds.
"""

from __future__ import annotations

import re

from ..ingestion.keywords import _STOPWORDS

_WORD = re.compile(r"\w+", re.UNICODE)


def _content(words: list[str]) -> list[str]:
    return [w for w in words if len(w) > 2 and w.lower() not in _STOPWORDS]


def user_worded_query(query: str, user_texts: list[str]) -> str | None:
    """``query`` cut to the words the user typed, or ``None`` to skip the search.

    ``None`` when more than half of the query's meaningful words were not the
    user's: what is left no longer searches for what the model meant, and a
    search for a different thing is worse than none. A reformulation that only
    adds a word or two ("coverage" for "cover") keeps the rest and still works.
    """
    allowed = {w.lower() for text in user_texts for w in _WORD.findall(text or "")}
    words = _WORD.findall(query or "")
    kept = [w for w in words if w.lower() in allowed]
    meaningful, kept_meaningful = _content(words), _content(kept)
    if not kept_meaningful or len(kept_meaningful) * 2 < len(meaningful):
        return None
    return " ".join(kept)
