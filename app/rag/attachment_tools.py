"""The `read_file` tool: let the model page through a long attachment.

Why a tool rather than a bigger prompt
-------------------------------------
A 300-page contract does not fit in a context window, and the alternative to
paging is a hard truncation -- which answers a question about the contract
from its first N characters and presents that as an answer about the whole
thing. That is the "partial result that looks complete" failure CLAUDE.md §2
names, and on an attachment it is worse than on a synced source: the asker is
holding the document, so a half-read answer is one they will believe and
cannot check.

Why the slice is in MEMORY, not a query
---------------------------------------
`load_attachment_texts` already returns the full text in the one query the
turn was going to make anyway -- 0.4MB at the ceiling. So paging costs nothing
at the database and this module never touches it, which is also what keeps
`app/rag/` from importing `app/attachments/`.

Bounded like every other walk here: ``max_reads`` calls of ``max_read_chars``
in ONE round, never a loop (the rule GitHubAgent already sets).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Files are addressed by 1-based INDEX, not filename: two uploads can share a
#: name, and the index is what the preview block already labels them with, so
#: the model is choosing from something it can see.
READ_FILE_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a section of one of the attached files by character offset. "
            "Use this to find the part of a long file that answers the "
            "question. Call it several times in one go to read several "
            "sections or several files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "integer",
                    "description": "Which attached file, as numbered in ATTACHED FILES.",
                },
                "start_char": {
                    "type": "integer",
                    "description": "Character offset to start from (0 is the beginning).",
                },
                "num_chars": {
                    "type": "integer",
                    "description": "How many characters to return.",
                },
            },
            "required": ["file_id"],
        },
    },
}


@dataclass(frozen=True)
class AttachedFile:
    """One attachment as the paging round sees it."""

    filename: str
    text: str
    #: The file was longer than the extractor's own budget, so even the stored
    #: text is partial. Carried through to the prompt -- a file cut twice must
    #: not be described as read once.
    truncated: bool


def build_preview_block(files: list[AttachedFile], preview_chars: int) -> str:
    """The ATTACHED FILES header: what exists, how big, and its opening.

    The length is stated because the model cannot otherwise choose a sensible
    ``start_char`` -- without it, paging is guesswork against an unknown end.
    """
    lines = []
    for i, f in enumerate(files, start=1):
        head = f.text[:preview_chars].strip()
        note = " (the file itself was too long to store in full)" if f.truncated else ""
        lines.append(
            f"[{i}] {f.filename} — {len(f.text)} characters{note}\n"
            f"Beginning of the file:\n{head}"
        )
    return "\n\n".join(lines)


def run_reads(
    files: list[AttachedFile],
    calls: list[tuple[str, dict]],
    *,
    max_reads: int,
    max_read_chars: int,
) -> list[str]:
    """Execute the model's `read_file` calls. Returns context strings.

    Every argument is treated as untrusted: a bad ``file_id`` is skipped
    rather than raising, and the offsets are clamped into the text. A model
    asking for character 900000 of a 40000-character file is a mistake to
    absorb, not a 500 -- the answer path must survive it.
    """
    contexts: list[str] = []
    for name, args in calls[:max_reads]:
        if name != "read_file":
            continue
        try:
            index = int(args.get("file_id", 0))
            start = max(0, int(args.get("start_char", 0)))
            length = int(args.get("num_chars", max_read_chars))
        except (TypeError, ValueError):
            continue
        if not 1 <= index <= len(files):
            continue
        length = max(1, min(length, max_read_chars))
        chosen = files[index - 1]
        excerpt = chosen.text[start : start + length]
        if not excerpt.strip():
            continue
        end = start + len(excerpt)
        # The RANGE is stated so the model cannot present an excerpt as the
        # whole file, and so a later "is that everything?" is answerable.
        contexts.append(
            f"Attached file: {chosen.filename} "
            f"(characters {start}–{end} of {len(chosen.text)})\n\n{excerpt}"
        )
    return contexts
