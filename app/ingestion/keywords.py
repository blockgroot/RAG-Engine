"""Keyword extraction without an LLM: term frequency over the chunk's words,
appended to the stored text so exact-term keyword search has one more surface.
"""

from __future__ import annotations

import re
from collections import Counter

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-']{2,}")

# Deliberately small and generic (not corpus-specific) — this only needs to
# suppress function words so frequency counting surfaces real topic terms.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "are", "but", "not", "you", "your", "with",
        "this", "that", "have", "has", "had", "was", "were", "will",
        "can", "may", "must", "shall", "should", "would", "could",
        "from", "into", "onto", "than", "then", "them", "they", "their",
        "its", "our", "ours", "who", "whom", "which", "what", "when",
        "where", "how", "why", "all", "any", "each", "few", "more",
        "most", "other", "some", "such", "only", "own", "same", "than",
        "too", "very", "just", "also", "about", "above", "after",
        "again", "against", "before", "being", "below", "between",
        "both", "during", "further", "here", "there", "over", "under",
        "once", "off", "out", "does", "did", "doing", "these", "those",
        "per", "via", "etc", "within", "without", "upon", "including",
    }
)


def extract_keywords(text: str, top_n: int = 6) -> list[str]:
    """Return up to ``top_n`` frequent, non-trivial words from ``text``.

    Counted case-folded, written in first-seen casing ("PTO"); ties by first
    occurrence, so output is deterministic.
    """
    if top_n <= 0:
        return []

    counts: Counter[str] = Counter()
    first_seen_form: dict[str, str] = {}
    first_seen_index: dict[str, int] = {}
    for i, match in enumerate(_WORD_RE.finditer(text)):
        word = match.group(0)
        low = word.lower()
        if low in _STOPWORDS:
            continue
        counts[low] += 1
        if low not in first_seen_form:
            first_seen_form[low] = word
            first_seen_index[low] = i

    ranked = sorted(counts, key=lambda w: (-counts[w], first_seen_index[w]))
    return [first_seen_form[w] for w in ranked[:top_n]]


def append_keyword_line(stored_text: str, source_text: str, top_n: int = 6) -> str:
    """Append a ``Keywords: ...`` line (extracted from ``source_text``) to
    ``stored_text``, or return ``stored_text`` unchanged if none are found.

    Separate so keywords come from the raw chunk, not the generated context.
    """
    keywords = extract_keywords(source_text, top_n)
    if not keywords:
        return stored_text
    return f"{stored_text}\n\nKeywords: {', '.join(keywords)}"
