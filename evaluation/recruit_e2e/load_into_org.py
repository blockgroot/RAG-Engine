#!/usr/bin/env python3
"""Load the recruit workbook into an org as a Drive document, without Drive.

The same steps as `evaluation/sales_e2e/load_into_org.py`: the real
`ingest_source` over the file, read with the Drive adapter's own workbook reader
(`_workbook_document`, what a Google Sheet's .xlsx export goes through), so only
the download is skipped. Shared with one viewer, as a private Drive file is.

Usage:
    .venv/bin/python evaluation/recruit_e2e/load_into_org.py <org_id> <viewer_email>
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.db import close_pool  # noqa: E402
from app.ingestion.pipeline import ingest_source  # noqa: E402
from app.rag.query_cache import delete_org_entries  # noqa: E402
from app.sources.base import DocAccess, SourceDocument, SourceRef  # noqa: E402
from app.sources.google_drive import _workbook_document  # noqa: E402

BOOK = Path(__file__).resolve().parent / "Recruit Lead Tracker 2026.xlsx"
EXTERNAL_ID = "recruit-e2e-tracker"
STAMP = datetime(2026, 11, 5, tzinfo=timezone.utc)


class LocalWorkbook:
    listing_complete = False  # not the org's Drive: never delete what it does not list

    def __init__(self, viewer: str) -> None:
        self._access = DocAccess.restricted([viewer])

    def list_documents(self) -> list[SourceRef]:
        return [SourceRef(external_id=EXTERNAL_ID, title=BOOK.stem, last_modified=STAMP,
                          access=self._access)]

    def fetch_document(self, external_id: str) -> SourceDocument:
        content, tables = _workbook_document(BOOK.read_bytes(), name=BOOK.stem)
        return SourceDocument(external_id=external_id, title=BOOK.stem, content=content,
                              last_modified=STAMP, access=self._access, tables=tables)

    def get_last_modified(self, external_id: str) -> datetime | None:
        return STAMP


if __name__ == "__main__":
    org, viewer = sys.argv[1], sys.argv[2]
    from app.jobs.worker import _build_graph, _record_insight_facts

    started = time.perf_counter()
    try:
        result = ingest_source(LocalWorkbook(viewer), org, provider="google", incremental=False)
        print(result, f"{time.perf_counter() - started:.1f}s")
        delete_org_entries(org)
        _record_insight_facts(org, "google", None)
        _build_graph(org, "google", None, result)
    finally:
        close_pool()
