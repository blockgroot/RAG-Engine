"""Whether one extracted file is small enough to put in a prompt.

Checked at upload, so the member can pick a shorter file instead of getting
an answer that saw part of it. Counted with the chunking heuristic
(``chunk_tokens.count_tokens``), not a model tokenizer: no need to be exact.
"""

from __future__ import annotations

from ..config.settings import AttachmentSettings
from .extract import kind_for

#: Kinds that skip the token ceiling: spreadsheets are long, repetitive, and
#: asked narrow questions.
TOKEN_EXEMPT_KINDS = frozenset({"csv", "xlsx"})


def token_rejection_reason(
    filename: str, text: str, settings: AttachmentSettings | None = None
) -> str | None:
    """Why this file is too large for a prompt (naming the file, the size and
    what to do), or None when it fits.
    """
    settings = settings or AttachmentSettings.from_env()
    if not settings.max_tokens:  # 0 disables the gate (Onyx's convention)
        return None
    if kind_for(filename) in TOKEN_EXEMPT_KINDS:
        return None

    from ..ingestion.chunk_tokens import count_tokens

    tokens = count_tokens(text)
    if tokens <= settings.max_tokens:
        return None
    return (
        f"{filename} is about {tokens:,} tokens, over the {settings.max_tokens:,} "
        "limit for one file. Split it or attach the relevant section."
    )
