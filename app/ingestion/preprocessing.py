"""Normalize text before chunking so the chunker splits on real boundaries.

Input is text or Markdown (adapters convert). Normalizes line endings, strips
trailing whitespace, collapses 3+ blank lines to one; tables and headings stay
whole blocks.
"""

from __future__ import annotations

import re

_MULTI_BLANK = re.compile(r"\n{3,}")


def preprocess(text: str) -> str:
    """Normalize raw document text ahead of chunking."""
    if not text:
        return ""

    # Normalize line endings.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Strip trailing whitespace per line (keeps leading indentation for tables).
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    # Collapse runs of blank lines to a single blank line (one paragraph break).
    text = _MULTI_BLANK.sub("\n\n", text)

    return text.strip()
