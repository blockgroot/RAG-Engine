#!/usr/bin/env python3
"""Load the sales test files into an org as Drive documents, without Drive.

Runs the real `ingest_source` (chunk, embed, tables, guard) over the files in
upload_to_drive/, read with the Drive adapter's own CSV/DOCX readers, so only
the download is skipped. Shared with one viewer, as a private Drive file would be.

`listing_complete = False` because this listing is NOT the org's Drive: without it
the ingest would delete every real Drive document it does not list.

Usage:
    .venv/bin/python evaluation/sales_e2e/load_into_org.py <org_id> <viewer_email>
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from app.db import close_pool  # noqa: E402
from app.ingestion.pipeline import ingest_source  # noqa: E402
from app.sources.base import DocAccess, SourceDocument, SourceRef  # noqa: E402
from app.sources.google_drive import _extract_docx_text, _table_document  # noqa: E402

FILES = Path(__file__).resolve().parent / "upload_to_drive"
STAMP = datetime(2026, 11, 3, tzinfo=timezone.utc)


class LocalFiles:
    listing_complete = False  # see module docstring

    def __init__(self, viewer: str) -> None:
        self._access = DocAccess.restricted([viewer])
        self._paths = {f"sales-e2e-{p.stem.lower().replace(' ', '-')}": p for p in sorted(FILES.iterdir())}

    def list_documents(self) -> list[SourceRef]:
        return [SourceRef(external_id=k, title=p.stem, last_modified=STAMP, access=self._access)
                for k, p in self._paths.items()]

    def fetch_document(self, external_id: str) -> SourceDocument:
        p = self._paths[external_id]
        tables = None
        if p.suffix == ".csv":
            content, tables = _table_document(p.read_text(), name=p.stem, sheet=False)
        else:
            content = _extract_docx_text(p.read_bytes())
        return SourceDocument(external_id=external_id, title=p.stem, content=content,
                              last_modified=STAMP, access=self._access, tables=tables)

    def get_last_modified(self, external_id: str) -> datetime | None:
        return STAMP


if __name__ == "__main__":
    org, viewer = sys.argv[1], sys.argv[2]
    from app.jobs.worker import _build_graph, _record_insight_facts

    try:
        # incremental=False so a rerun re-reads the files; the steps after it are
        # the sync job's own (app/jobs/worker.py::run_once)
        result = ingest_source(LocalFiles(viewer), org, provider="google", incremental=False)
        print(result)
        _record_insight_facts(org, "google", None)
        _build_graph(org, "google", None, result)
    finally:
        close_pool()
