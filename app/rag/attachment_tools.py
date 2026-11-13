"""The `read_file` tool: let the model page through a long attachment.

Truncating a long file would answer from its first N characters as if it
were the whole: the "partial result that looks complete" failure. The full
text is already in memory, so paging costs no query (and keeps `app/rag/`
off `app/attachments/`). Bounded: ``max_reads`` calls of
``max_read_chars`` in ONE round.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Files are addressed by 1-based INDEX (names can repeat), as the preview
#: labels them.
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
    #: The extractor already cut the stored text; said in the prompt.
    truncated: bool


def build_preview_block(files: list[AttachedFile], preview_chars: int) -> str:
    """The ATTACHED FILES header: what exists, its length (so ``start_char`` is
    not guesswork) and its opening."""
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

    Arguments are untrusted: a bad ``file_id`` is skipped and offsets are
    clamped, never a 500.
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
        # The RANGE is stated so an excerpt is never presented as the whole file.
        contexts.append(
            f"Attached file: {chosen.filename} "
            f"(characters {start}–{end} of {len(chosen.text)})\n\n{excerpt}"
        )
    return contexts
