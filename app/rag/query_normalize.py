"""Lightweight query spelling normalization.

A typo'd question can rank the right chunk mid-pool, and an always-on LLM
rewrite would cost every request. So: SymSpell against the org's own chunk
vocabulary, seeded with common query words.

- Local, deterministic, milliseconds; corrects toward document terms.
- Seeded question words are never "corrected" to a rare corpus near-miss;
  corpus terms are boosted so real domain typos prefer document spellings.
- Only OOV tokens with a suggestion within max edit distance (default 1).
- Capitalized OOV tokens (names) are left alone.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable

from symspellpy import SymSpell, Verbosity

from ..config.settings import QueryNormSettings

logger = logging.getLogger(__name__)

_TOKEN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+|[^\w\s]", re.UNICODE)

# Question English that terse policy text rarely holds; without it SymSpell
# maps "many" to "main". Query-shaped, not a full dictionary.
_COMMON_QUERY_ENGLISH: frozenset[str] = frozenset(
    {
        "about",
        "after",
        "again",
        "also",
        "always",
        "before",
        "between",
        "both",
        "could",
        "does",
        "during",
        "each",
        "every",
        "from",
        "have",
        "here",
        "into",
        "just",
        "like",
        "many",
        "more",
        "most",
        "much",
        "need",
        "never",
        "only",
        "other",
        "over",
        "same",
        "should",
        "some",
        "such",
        "than",
        "that",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "under",
        "until",
        "very",
        "want",
        "what",
        "when",
        "where",
        "which",
        "while",
        "with",
        "within",
        "without",
        "would",
        "your",
        # Common query verbs/nouns that policies often conjugate differently
        "provide",
        "includes",
        "include",
        "allow",
        "allows",
        "cover",
        "covers",
        "require",
        "requires",
        "receive",
        "request",
        "apply",
        "submit",
        "approve",
        "change",
        "offer",
        "offers",
        "plan",
        "plans",
        "company",
        "employee",
        "employees",
        "insurance",
        "remote",
        "remotely",
        "unused",
        "carried",
        "carry",
    }
)

# Corpus entries get this floor so they outrank English-seed ties on edit distance.
_CORPUS_FREQ_BOOST = 10
_ENGLISH_SEED_FREQ = 2



def _looks_like_inflection_variant(original: str, suggestion: str) -> bool:
    """True when the suggestion only appends a verbal/adverbial inflection
    (provide→provided). A trailing ``s``/``es`` is not counted: that also fixes
    truncated typos (wellnes→wellness)."""
    a, b = original.lower(), suggestion.lower()
    if a == b:
        return True
    # Only refuse lengthening the token (stem → inflected corpus form).
    if len(b) <= len(a) or not b.startswith(a):
        return False
    suffix = b[len(a) :]
    return suffix in {"ed", "d", "ing", "er", "est", "ly"}



def _is_wordish(tok: str) -> bool:
    """True for words/numbers, including apostrophe forms (Bupa's)."""
    if not tok:
        return False
    if tok.isalnum() or tok.isalpha():
        return True
    # Contractions / possessives tokenized as a single piece.
    stripped = tok.replace("'", "")
    return bool(stripped) and stripped.isalpha()


def tokenize_preserving(text: str) -> list[str]:
    """Split into word / number / punctuation tokens (whitespace between words)."""
    return _TOKEN.findall(text)


def build_vocab_counts(texts: Iterable[str], *, min_word_length: int) -> Counter[str]:
    """Lowercased alphabetic tokens from document texts, with frequencies."""
    counts: Counter[str] = Counter()
    for text in texts:
        for tok in tokenize_preserving(text):
            if tok.isalpha() and len(tok) >= min_word_length:
                counts[tok.lower()] += 1
    return counts


class CorpusSpellNormalizer:
    """Per-org SymSpell corrector built from that org's chunk text.

    ``_by_org`` is an LRU bounded at ``settings.cache_max_orgs``; an evicted
    org rebuilds its dictionary on its next query.
    """

    def __init__(self, settings: QueryNormSettings | None = None) -> None:
        self._settings = settings or QueryNormSettings.from_env()
        self._lock = threading.Lock()
        self._by_org: OrderedDict[str, SymSpell] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def clear_cache(self, org_id: str | None = None) -> None:
        with self._lock:
            if org_id is None:
                self._by_org.clear()
            else:
                self._by_org.pop(org_id, None)

    def normalize(
        self,
        question: str,
        org_id: str,
        corpus_texts: Iterable[str] | Callable[[], Iterable[str]],
    ) -> str:
        """Return ``question`` with OOV tokens corrected toward corpus vocab.

        Unchanged when disabled or the corpus is empty. Never raises.
        ``corpus_texts`` may be a callable, read only on a dictionary cache miss
        (pass it that way); a plain iterable also works.
        """
        if not self._settings.enabled or not question.strip():
            return question
        try:
            sym = self._symspell_for(org_id, corpus_texts)
        except Exception:  # noqa: BLE001 — never fail the query path
            logger.exception("query normalization dictionary build failed")
            return question
        if sym is None or sym.word_count == 0:
            return question

        max_ed = self._settings.max_edit_distance
        min_len = self._settings.min_word_length
        out: list[str] = []
        # Reconstruct with single spaces between word tokens; keep punctuation glued.
        parts = tokenize_preserving(question)
        for i, tok in enumerate(parts):
            if tok.isalpha() and len(tok) >= min_len:
                lower = tok.lower()
                if lower in sym.words:
                    out.append(tok)
                elif tok[0].isupper():
                    # Capitalized OOV: never invent a near-miss (Niva→five).
                    out.append(tok)
                else:
                    suggestions = sym.lookup(
                        lower, Verbosity.CLOSEST, max_edit_distance=max_ed
                    )
                    if suggestions:
                        fixed = suggestions[0].term
                        if _looks_like_inflection_variant(lower, fixed):
                            out.append(tok)
                            continue
                        out.append(fixed)
                    else:
                        out.append(tok)
            else:
                out.append(tok)

        # No space before bare punctuation; spaces between word-like tokens
        # (str.isalpha() is False for "Bupa's").
        buf: list[str] = []
        for tok in out:
            if not buf:
                buf.append(tok)
                continue
            if _is_wordish(tok):
                if _is_wordish(buf[-1]):
                    buf.append(" ")
                buf.append(tok)
            else:
                buf.append(tok)
        return "".join(buf)

    def _symspell_for(
        self,
        org_id: str,
        corpus_texts: Iterable[str] | Callable[[], Iterable[str]],
    ) -> SymSpell | None:
        with self._lock:
            cached = self._by_org.get(org_id)
            if cached is not None:
                self._by_org.move_to_end(org_id)
                return cached

        # Only now is the corpus needed: the early return above is the common case.
        texts = corpus_texts() if callable(corpus_texts) else corpus_texts

        counts = build_vocab_counts(
            texts, min_word_length=self._settings.min_word_length
        )
        if not counts:
            return None

        sym = SymSpell(
            max_dictionary_edit_distance=self._settings.max_edit_distance,
            prefix_length=7,
        )
        for word, count in counts.items():
            sym.create_dictionary_entry(word, count + _CORPUS_FREQ_BOOST)
        min_len = self._settings.min_word_length
        for word in _COMMON_QUERY_ENGLISH:
            if len(word) < min_len or word in counts:
                continue
            sym.create_dictionary_entry(word, _ENGLISH_SEED_FREQ)

        with self._lock:
            self._by_org[org_id] = sym
            self._by_org.move_to_end(org_id)
            while len(self._by_org) > self._settings.cache_max_orgs:
                self._by_org.popitem(last=False)
        return sym
