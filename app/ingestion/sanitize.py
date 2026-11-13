"""Ingest-time content safeguards, before chunking.

Caps document size, strips NUL and C0 controls (tab/newline kept), and
rejects mostly non-text documents. No malware or HTML stripping.
"""

from __future__ import annotations

import re

from ..config.settings import IngestSanitizeSettings
from ..core.exceptions import ProviderError

_C0_DISALLOWED = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_ingest_text(
    text: str, settings: IngestSanitizeSettings | None = None
) -> str:
    """Validate and normalize raw text before ``preprocess`` / chunking."""
    settings = settings or IngestSanitizeSettings.from_env()
    if text is None:
        raise ProviderError("Document content is missing")
    if len(text) > settings.max_document_chars:
        raise ProviderError(
            f"Document exceeds maximum size ({settings.max_document_chars} characters)"
        )
    if not text.strip():
        return ""

    sample = text[: min(len(text), 50_000)]
    bad = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\t\n\r")
    if sample and (bad / len(sample)) > settings.max_control_char_ratio:
        raise ProviderError("Document appears to contain malformed or binary content")

    cleaned = _C0_DISALLOWED.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    if not cleaned.strip():
        raise ProviderError("Document contains no usable text after sanitization")

    return cleaned
