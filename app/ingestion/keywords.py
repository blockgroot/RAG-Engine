"""Non-LLM keyword extraction (metadata-creation gap #2, the cheap half).

Hypothetical questions (``contextualize.py``) already cover "let a rephrased
user question match a chunk's topic," but they need an LLM call. Keywords
are a distinct, purely deterministic metadata signal — a simple term-
frequency extraction over the chunk's own words, filtered against a small
stopword list — appended to the stored chunk text so an exact-term BM25
lookup (a form code, a benefit name, "part-time") has one more surface to
match against. Zero LLM calls, zero added latency; runs once per chunk at
ingest, same as every other ingest-time step here.
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

    Case-folded for counting, original casing of the FIRST occurrence is
    kept in the output (a term like "PTO" or "HR" reads better than "pto").
    Ties broken by first-occurrence order, so output is deterministic.
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

    Extraction and the text being stored are separate parameters so a
    contextualized chunk (LLM-prefixed context, possible questions) can have
    its keywords extracted from the ORIGINAL raw chunk instead — the added
    situating-context sentence would otherwise dilute the word frequencies
    with generic terms that aren't actually the chunk's topic.
    """
    keywords = extract_keywords(source_text, top_n)
    if not keywords:
        return stored_text
    return f"{stored_text}\n\nKeywords: {', '.join(keywords)}"
