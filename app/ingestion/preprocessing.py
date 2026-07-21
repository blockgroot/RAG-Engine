"""Document preprocessing — run before chunking.

Why this exists
---------------
Splitting raw text blindly by a fixed character count mangles real policy
documents: it cuts through the middle of Markdown tables, separates a heading
from the paragraph it introduces, and leaves ragged whitespace that pollutes
embeddings. This step normalizes the text so the chunker can split on *natural*
structural boundaries (blank lines between paragraphs, table blocks, headings).

Scope / tradeoff for this phase
-------------------------------
We assume the input is already text or Markdown (the format most policy exports
and Markdown conversions produce). We deliberately do NOT do layout-aware
extraction from PDFs/DOCX/HTML here (tools like Unstructured or Docling) — that
belongs to a later "ingestion adapters" phase. What we do now:

- normalize line endings to ``\n``
- strip trailing whitespace on each line
- collapse 3+ consecutive blank lines down to a single blank line

This keeps Markdown tables and headings intact as whole blocks so the chunker
treats each as one unit rather than slicing through them.
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
