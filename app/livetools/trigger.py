"""WHEN a live read is worth its ~2 s (mode A only). No extra model call:

1. Does the question ask about CURRENT state? ``AskIntent.needs_live`` from
   the classifier already run (no word list). No verdict: the read goes ahead.
2. Is the synced copy fresh? A tool synced successfully within
   ``FRESH_SECONDS`` is not read (``gateway``).

Mode B (the refusal path) is not gated.
"""

from __future__ import annotations


def wants_live(needs_live: bool | None) -> bool:
    """Only an explicit "no" from the classifier skips the read."""
    return needs_live is not False
