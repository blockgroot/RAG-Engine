"""Whether one extracted file is small enough to put in a prompt.

Onyx runs this gate at UPLOAD (`projects_file_utils.categorize_uploaded_files`)
and we did not, which is the difference that matters: `max_chars` bounds the
DATABASE, `max_tokens` bounds the PROMPT, and the prompt is the thing that
actually fails. Counting here means the member is told at the moment they can
still do something about it -- pick a shorter file -- rather than discovering
it later as an answer that quietly saw only part of their document.

The count comes from `ingestion.chunk_tokens.count_tokens`, the heuristic this
codebase already uses for chunking. NOT a model tokenizer: CLAUDE.md §5 --
the BGE-M3 tokenizer was 325MB, 64% of the box, and `transformers` is out of
the deploy image. A gate that must reject a 300k-token file does not need to
be right to the token.
"""

from __future__ import annotations

from ..config.settings import AttachmentSettings
from .extract import kind_for

#: Kinds that skip the token ceiling entirely. Onyx's `_skip_token_threshold`
#: over `TABULAR_EXTENSIONS`, and the reasoning carries: a spreadsheet is
#: legitimately long, its rows are short and repetitive, and it is normally
#: asked narrow questions ("what's the total for March?") that paging answers
#: well. Rejecting one for length would refuse the format this gate is least
#: likely to be protecting anyone from.
TOKEN_EXEMPT_KINDS = frozenset({"csv"})


def token_rejection_reason(
    filename: str, text: str, settings: AttachmentSettings | None = None
) -> str | None:
    """Why this file is too large for a prompt, or None when it fits.

    The message names the file, the measurement and what to do, because a bare
    "too large" leaves someone re-uploading the same document to find out.
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
