"""Tables inside a file someone uploaded to a chat, for Chart mode.

An upload is not a document: nobody indexed it, it has no sharing to copy, and
it belongs to one person in one conversation. So its tables are stored beside
documents' tables (same rows, same query code, same checks) but hang off the
ATTACHMENT, and only that person in that chat can read them
(``store.UploadScope``).

Two passes, like a document's:

- ``read_upload_tables`` runs at upload, no AI. It reads the tables the file
  already has -- CSV/TSV rows, each Excel sheet, Word tables, tables PDF pages
  draw, pipe tables in text/markdown -- and hands them to the same
  ``TableAdapter`` ingestion uses, so a column is typed one way everywhere.
  It never fails the upload: a file whose tables cannot be read still answers
  questions as text.
- ``read_upload_figures`` runs in Chart mode, on demand, for a file with NO
  table: the text adapter reads figures out of its sentences and code keeps a
  row only when every cell is in its quote. Once per file
  (``figures_read_at``), because the asker is waiting and the answer does not
  change.
"""

from __future__ import annotations

import io
import logging

from ..core.exceptions import ProviderError
from ..db.connection import get_connection
from . import extract
from .base import DocumentText
from .store import UploadScope, replace_attachment_tables
from .table_adapter import TableAdapter

logger = logging.getLogger(__name__)

#: Files read for figures in one Chart-mode question. The asker is waiting.
MAX_FIGURE_READS = 2


def _csv_tables(filename: str, data: bytes) -> list[extract.RawTable]:
    raw = extract.parse_csv(data.decode("utf-8", errors="replace"), name=filename)
    return [raw] if raw is not None else []


def _xlsx_tables(filename: str, data: bytes) -> list[extract.RawTable]:
    from ..attachments.extract import xlsx_sheets

    sheets, more = xlsx_sheets(data, extract.MAX_ROWS + 1)
    out = []
    for name, rows, cut in sheets:
        if len(rows) < extract.MIN_ROWS + 1:
            continue
        notes = (f"Only the first {extract.MAX_ROWS} rows of this sheet were read.",) if cut else ()
        if more:
            notes += (f"Only the first {len(sheets)} sheets of this workbook were read.",)
        out.append(extract.RawTable(
            name=f"{filename} · {name}" if len(sheets) > 1 else filename,
            header=rows[0], rows=rows[1:], truncated=cut, notes=notes,
        ))
    return out


def _docx_tables(filename: str, data: bytes) -> list[extract.RawTable]:
    import docx  # python-docx

    document = docx.Document(io.BytesIO(data))
    return extract.find_markdown_tables(extract.render_docx_tables(document), title=filename)


def _pdf_tables(filename: str, data: bytes, max_pages: int) -> list[extract.RawTable]:
    """Tables a PDF page DRAWS (ruled or aligned), via pdfplumber. Plain text
    extraction flattens a table into a run of numbers with no columns, which is
    exactly what a chart cannot use."""
    import pdfplumber

    out: list[extract.RawTable] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = pdf.pages
        cut = len(pages) > max_pages
        for number, page in enumerate(pages[:max_pages], start=1):
            try:
                found = page.extract_tables()
            except Exception:  # noqa: BLE001 - one bad page must not lose the rest
                logger.warning("uploads: a PDF page's tables could not be read", exc_info=True)
                continue
            for rows in found:
                rows = [[(c or "").replace("\n", " ").strip() for c in r] for r in rows]
                rows = [r for r in rows if any(r)]
                if len(rows) < extract.MIN_ROWS + 1:
                    continue
                notes = (f"Only the first {max_pages} pages of this PDF were read.",) if cut else ()
                out.append(extract.RawTable(
                    name=f"{filename} (page {number})", header=rows[0], rows=rows[1:],
                    notes=notes,
                ))
                if len(out) >= extract.MAX_TABLES_PER_DOCUMENT:
                    return out
    return out


def raw_tables(filename: str, data: bytes, text: str, *, max_pdf_pages: int) -> list[extract.RawTable]:
    """Every table the file already has, as header + rows of strings."""
    from ..attachments.extract import kind_for

    kind = kind_for(filename)
    if kind == "csv":
        return _csv_tables(filename, data)
    if kind == "xlsx":
        return _xlsx_tables(filename, data)
    if kind == "docx":
        return _docx_tables(filename, data)
    if kind == "pdf":
        return _pdf_tables(filename, data, max_pdf_pages)
    return extract.find_markdown_tables(text, title=filename)


def read_upload_tables(
    attachment_id: str, *, org_id: str, user_id: str, filename: str, data: bytes,
    text: str, max_pdf_pages: int,
) -> int:
    """Store the tables an uploaded file already has. Returns how many.

    Never raises: the upload has already succeeded as text, and a file whose
    tables could not be read is still a file the asker can ask about.
    """
    try:
        raw = raw_tables(filename, data, text, max_pdf_pages=max_pdf_pages)
        tables = TableAdapter().extract(DocumentText(
            external_id=attachment_id, title=filename, content=text, tables=tuple(raw),
        ))
        if tables:
            replace_attachment_tables(attachment_id, org_id=org_id, user_id=user_id,
                                      tables=tables)
        return len(tables)
    except Exception:  # noqa: BLE001 - a later chart, never a failed upload
        logger.warning("uploads: could not read tables from %s", filename, exc_info=True)
        return 0


def _unread(org_id: str, uploads: UploadScope) -> list[tuple[str, str]]:
    """``(attachment_id, filename)`` of this person's files in this chat that
    have no table and have never been read for figures, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT a.id::text, a.filename FROM conversation_attachments a
             WHERE a.org_id = %s AND a.conversation_id = %s AND a.user_id = %s
               AND a.figures_read_at IS NULL
               AND NOT EXISTS (SELECT 1 FROM doc_tables t WHERE t.attachment_id = a.id)
             ORDER BY a.created_at DESC
             LIMIT %s
            """,
            (org_id, uploads.conversation_id, uploads.user_id, MAX_FIGURE_READS),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _mark_read(attachment_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE conversation_attachments SET figures_read_at = now() WHERE id = %s",
            (attachment_id,),
        )
        conn.commit()


def read_upload_figures(*, org_id: str, uploads: UploadScope | None, adapter) -> int:
    """Read figures out of the prose of this chat's table-less uploads.

    ``adapter`` is the text adapter (``factory.build_upload_text_adapter``).
    A file is marked read even when nothing was found, so it costs one call
    ever; a failed call leaves it unread for the next question. Never raises.
    """
    if uploads is None or adapter is None:
        return 0
    try:
        pending = _unread(org_id, uploads)
    except Exception:  # noqa: BLE001
        logger.warning("uploads: could not list files to read", exc_info=True)
        return 0
    if not pending:
        return 0

    from ..attachments.store import load_attachment_texts

    texts = {a.id: a.content for a in load_attachment_texts(
        org_id=org_id, conversation_id=uploads.conversation_id, user_id=uploads.user_id)}
    found = 0
    for attachment_id, filename in pending:
        text = texts.get(attachment_id) or ""
        doc = DocumentText(external_id=attachment_id, title=filename, content=text)
        try:
            tables = adapter.extract(doc) if adapter.wants(doc) else []
            if tables:
                replace_attachment_tables(attachment_id, org_id=org_id,
                                          user_id=uploads.user_id, tables=tables,
                                          origin=adapter.origin)
            _mark_read(attachment_id)
            found += len(tables)
        except ProviderError:
            logger.warning("uploads: could not read figures from %s", filename, exc_info=True)
        except Exception:  # noqa: BLE001
            logger.exception("uploads: figure read failed for %s", filename)
    return found
