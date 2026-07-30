"""Compound-question detection and decomposition (Phase 18).

A single embedding of "Can I get X reimbursed, and what else is covered?" under-
represents the second informational need. When a question genuinely bundles
multiple asks, we split before retrieval and merge candidate pools afterward.

Ordinary questions (including "full-time and part-time leave") must not pay an
LLM decomposition call — a cheap heuristic gate comes first.
"""

from __future__ import annotations

import re

# Second clause looks like its own question (not a coordinated noun phrase).
_SECOND_ASK = re.compile(
    r"(?:"
    r",\s*and\s+(?P<a1>what|how|which|who|when|where|can|could|is|are|do|does|did|will|would)\b"
    r"|"
    r"\?\s+and\s+(?P<a2>what|how|which|who|when|where|can|could|is|are|do|does|did|will|would)\b"
    r"|"
    r"\band\s+what\s+else\b"
    r")",
    re.IGNORECASE,
)


def looks_compound(question: str) -> bool:
    """Fast, deterministic pre-check — false negatives defer to single-path retrieval."""
    q = question.strip()
    if not q:
        return False
    if q.count("?") >= 2:
        return True
    return _SECOND_ASK.search(q) is not None


def parse_sub_questions(raw: str, *, original: str) -> list[str]:
    """Parse LLM output into 1..N standalone sub-questions."""
    text = raw.strip()
    if not text or text.upper() == "SINGLE":
        return [original.strip()]
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    cleaned = [ln.lstrip("-•0123456789.) ").strip() for ln in lines]
    cleaned = [ln for ln in cleaned if ln and ln.upper() != "SINGLE"]
    if len(cleaned) <= 1:
        return [original.strip()]
    return cleaned
