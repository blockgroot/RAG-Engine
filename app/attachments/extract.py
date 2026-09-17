"""Uploaded file -> plain text. One implementation, deliberately.

No ``base.py``/``factory.py`` here: CLAUDE.md §2 asks for that shape when a
capability has (or will have) a second backend to abstract over, and text
extraction has neither. There is one way to read a PDF in this image and one
way to read a DOCX; an interface over a single implementation is the
speculative abstraction that section explicitly forbids.

Every extractor is BOUNDED and says when it stopped early. A truncated
document that looks complete is the failure this codebase is arranged against
(CLAUDE.md §2), and it is worse for an attachment than for an indexed source:
the asker is looking at the file, so a silent half-read produces an answer they
will believe and cannot check.
"""

from __future__ import annotations

import csv
import io
import logging

from ..core.exceptions import ProviderError

logger = logging.getLogger(__name__)


class AttachmentError(ProviderError):
    """An uploaded file could not be turned into text."""


#: Extension -> canonical kind. Matched on the FILENAME, not the browser's
#: content type: a browser routinely sends `application/octet-stream` for a
#: .docx, and trusting that would reject a file the user can plainly see is a
#: Word document. The extension is also attacker-controlled, but it only ever
#: selects a parser -- every parser is bounded and none of them execute
#: anything -- so the worst a wrong one can do is fail.
_KINDS = {
    "pdf": "pdf",
    "docx": "docx",
    "csv": "csv",
    "tsv": "csv",
    "txt": "text",
    "md": "text",
    "markdown": "text",
    "log": "text",
    "json": "text",
}

SUPPORTED_EXTENSIONS = tuple(sorted(_KINDS))


def kind_for(filename: str) -> str | None:
    """The parser this filename selects, or None when we cannot read it."""
    _, _, ext = filename.rpartition(".")
    return _KINDS.get(ext.lower()) if ext else None


def _decode(data: bytes) -> str:
    """Bytes -> str, never raising. Encoding is a property of someone else's
    file, so a mis-declared one must degrade to readable text rather than a
    500 the uploader cannot act on."""
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes, max_pages: int) -> tuple[str, bool]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise AttachmentError("That PDF could not be read", cause=exc) from exc

    if reader.is_encrypted:
        # An empty extraction from a locked PDF reads as "this file is blank",
        # which sends someone hunting the wrong problem.
        raise AttachmentError("That PDF is password-protected, so it can't be read")

    pages = reader.pages
    truncated = len(pages) > max_pages
    out: list[str] = []
    for page in pages[:max_pages]:
        try:
            out.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 - one bad page must not lose the rest
            logger.warning("Attachment: a PDF page could not be extracted", exc_info=True)
    return "\n\n".join(p for p in out if p.strip()), truncated


def _extract_docx(data: bytes) -> tuple[str, bool]:
    import docx  # python-docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise AttachmentError("That Word file could not be read", cause=exc) from exc

    parts = [p.text for p in document.paragraphs if p.text.strip()]
    # Tables carry the answer often enough in a real document (a rate card, a
    # rota) that dropping them would make the feature look broken on exactly
    # the files people upload to ask about numbers.
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts), False


def _extract_csv(data: bytes, max_rows: int) -> tuple[str, bool]:
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # a single-column file sniffs as nothing
    rows = list(csv.reader(io.StringIO(text), dialect))
    truncated = len(rows) > max_rows
    # Rendered as delimited lines rather than JSON: the model reads a table
    # better as a table, and it is a third of the tokens.
    return "\n".join(" | ".join(c.strip() for c in r) for r in rows[:max_rows]), truncated


def extract_text(
    filename: str,
    data: bytes,
    *,
    max_chars: int,
    max_pdf_pages: int,
    max_csv_rows: int,
) -> tuple[str, bool]:
    """``(text, truncated)`` for an uploaded file.

    ``truncated`` is True when the file was longer than one of the bounds --
    pages, rows or characters. The caller must surface it; see the schema note
    on ``conversation_attachments.truncated``.
    """
    kind = kind_for(filename)
    if kind is None:
        raise AttachmentError(
            f"{filename} isn't a file type I can read "
            f"({', '.join(SUPPORTED_EXTENSIONS)})"
        )

    if kind == "pdf":
        text, truncated = _extract_pdf(data, max_pdf_pages)
    elif kind == "docx":
        text, truncated = _extract_docx(data)
    elif kind == "csv":
        text, truncated = _extract_csv(data, max_csv_rows)
    else:
        text, truncated = _decode(data), False

    text = text.strip()
    if not text:
        # The overwhelmingly common cause is a scanned PDF, and saying so is
        # the difference between a user reaching for a different file and a
        # user assuming the product is broken. No OCR in this image.
        raise AttachmentError(
            f"No text could be read from {filename}. If it's a scan or an "
            "image-only PDF, there is no text layer to read."
        )

    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return text, truncated
