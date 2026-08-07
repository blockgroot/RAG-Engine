"""Lightweight query spelling normalization (Phase 17).

Why this shape
--------------
First-time / standalone questions were embedded raw. Only conversational
follow-ups get an LLM rewrite, and recovery spelling only fires *after* a
gate miss or refusal — so a typo'd but answerable question can rank the right
chunk mid-pool and never recover (ARCHITECTURE.md: "protien suppliments
reimbersed" at ~#18–24).

An always-on LLM rewrite would fix phrasing but add a permanent latency/cost
regression on every request — against this project's preference for the
cheapest mechanism that works (same reasoning as retrieval-reuse using cosine
instead of an LLM).

Choice: **SymSpell against the org's own chunk vocabulary** (``symspellpy``),
seeded with a small set of common English *query* words.
- Local, deterministic, milliseconds, no API key.
- Corrects toward *document* terms (reimbursed, supplements, …) — the
  vocabulary retrieval actually needs to match.
- Common question words (many, what, does, …) are seeded so they are never
  "corrected" toward a rare corpus near-miss (e.g. many→main when "main"
  appears once in a policy). Corpus terms are frequency-boosted so genuine
  domain typos still prefer document spellings when edit distance ties.
- Only replaces tokens that are missing from the combined vocab and have a
  close suggestion (edit distance ≤ configured max, default 1).
- Capitalized OOV tokens are left alone (named entities like Cigna/Niva);
  distance-2 was observed to map them onto unrelated corpus words.

Not in scope here: LLM rephrase toward document style, HyDE, or decomposition.
Those remain optional escalations if corpus-vocab spelling proves insufficient.
"""

from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from collections.abc import Callable, Iterable

from symspellpy import SymSpell, Verbosity

from ..config.settings import QueryNormSettings

logger = logging.getLogger(__name__)

_TOKEN = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?|\d+|[^\w\s]", re.UNICODE)

# High-frequency question English that rarely appears in terse policy text.
# Without these, SymSpell treats them as OOV and can map them onto a rare
# corpus near-miss (observed: "many" → "main"). Keep this list query-shaped,
# not a full dictionary — domain typos must still resolve to corpus terms.
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
    """True when suggestion only appends a verbal/adverbial inflection.

    Blocks provide→provided when the corpus uses a different tense. Does *not*
    treat a lone trailing ``s``/``es`` as inflection — that also matches
    truncated typos (wellnes→wellness) which we *do* want to fix. Plural ``s``
    damage is instead avoided by seeding common query nouns in
    ``_COMMON_QUERY_ENGLISH``.
    """
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
    """Per-org SymSpell corrector built from that org's stored chunk text."""

    def __init__(self, settings: QueryNormSettings | None = None) -> None:
        self._settings = settings or QueryNormSettings.from_env()
        self._lock = threading.Lock()
        self._by_org: dict[str, SymSpell] = {}

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

        If normalization is disabled or the corpus is empty, returns ``question``
        unchanged. Never raises — spelling is best-effort bookkeeping for recall.

        ``corpus_texts`` may be a **callable** returning the texts, which is how
        callers should pass it: the per-org dictionary is cached for the life of
        the process, so on every query after the first the texts were fetched,
        shipped over the wire, and thrown away unread. Passing a thunk means the
        corpus is only read on an actual cache miss. A plain iterable still
        works (tests, scripts) and behaves identically.
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
                    # Proper-noun / sentence-initial OOV: never invent a corpus
                    # near-miss (Niva→five, Compare→company). Typos we care about
                    # are almost always lowercase mid-question tokens.
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

        # Join: no space before bare punctuation; space between word-like tokens
        # (including apostrophe forms like Bupa's — str.isalpha() is False for those).
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
                return cached

        # Only now is the corpus actually needed. Resolving the thunk here
        # rather than at the call site is the whole point: the early return
        # above is taken on every query after the first.
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
        return sym
