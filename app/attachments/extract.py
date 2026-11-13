"""Uploaded file -> plain text. One implementation, so no base.py/factory.

Every extractor is bounded and says when it stopped early: a silent half-read
gives an answer the asker will believe.
"""

from __future__ import annotations

import csv
import io
import logging

from ..core.exceptions import ProviderError

logger = logging.getLogger(__name__)


class AttachmentError(ProviderError):
    """An uploaded file could not be turned into text."""


#: Extension -> kind, by filename (browsers send octet-stream for .docx). It
#: only picks a bounded, non-executing parser, so a wrong one just fails.
_KINDS = {
    "pdf": "pdf",
    "docx": "docx",
    "xlsx": "xlsx",
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
    # Tables often hold the answer (a rate card, a rota).
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


def _cell_text(value) -> str:
    """One spreadsheet cell as a person reads it: 12 not 12.0, a date not a
    datetime at midnight, nothing for an empty cell."""
    import datetime as _dt

    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, _dt.datetime):
        return value.date().isoformat() if value.time() == _dt.time() else value.isoformat(" ")
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value).strip()


#: Sheets read from one workbook. A bound, said out loud when it bites.
MAX_SHEETS = 10


def xlsx_sheets(data: bytes, max_rows: int) -> tuple[list[tuple[str, list[list[str]], bool]], bool]:
    """``([(sheet name, rows, truncated)], more sheets than read)`` for an
    .xlsx: values, not formulas; empty rows skipped; ``max_rows`` per sheet.

    The one workbook reader, so prompt text and chart rows always agree.
    """
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        raise AttachmentError("That Excel file could not be read", cause=exc) from exc
    try:
        sheets = []
        names = list(book.sheetnames)
        for name in names[:MAX_SHEETS]:
            rows: list[list[str]] = []
            truncated = False
            for values in book[name].iter_rows(values_only=True):
                cells = [_cell_text(v) for v in values]
                while cells and not cells[-1]:
                    cells.pop()
                if not any(cells):
                    continue
                if len(rows) >= max_rows:
                    truncated = True
                    break
                rows.append(cells)
            if rows:
                sheets.append((name, rows, truncated))
        return sheets, len(names) > MAX_SHEETS
    finally:
        book.close()


def _extract_xlsx(data: bytes, max_rows: int) -> tuple[str, bool]:
    sheets, more = xlsx_sheets(data, max_rows)
    blocks = []
    truncated = more
    for name, rows, cut in sheets:
        truncated = truncated or cut
        blocks.append(f"## {name}\n" + "\n".join(" | ".join(r) for r in rows))
    return "\n\n".join(blocks), truncated


def extract_text(
    filename: str,
    data: bytes,
    *,
    max_chars: int,
    max_pdf_pages: int,
    max_csv_rows: int,
) -> tuple[str, bool]:
    """``(text, truncated)`` for an uploaded file; the caller must surface
    ``truncated`` (pages, rows or characters over a bound).
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
    elif kind == "xlsx":
        text, truncated = _extract_xlsx(data, max_csv_rows)
    else:
        text, truncated = _decode(data), False

    text = text.strip()
    if not text:
        # Almost always a scanned PDF; say so. No OCR in this image.
        raise AttachmentError(
            f"No text could be read from {filename}. If it's a scan or an "
            "image-only PDF, there is no text layer to read."
        )

    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return text, truncated
