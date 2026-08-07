"""Wire formats shared between routers.

``job_payload`` exists because the ingestion-job dict was written out four
times (admin list/detail, workspace list/detail) — so adding a field meant
editing four places and any miss showed up as a field the UI silently never
received. The wire format lives here rather than on ``IngestionJob`` itself to
keep ``app/jobs/`` free of HTTP concerns.
"""

from __future__ import annotations

from ..jobs.queue import IngestionJob


def job_payload(job: IngestionJob) -> dict:
    """Serialize one ingestion job for the API.

    ``phase``/``total_documents``/``processed_documents`` are the live-progress
    fields: they change while ``status`` is still ``running``, which is what
    lets a poller render a truthful "3 of 17 pages" instead of an indefinite
    spinner.
    """
    return {
        "id": job.id,
        "connection_id": job.connection_id,
        "status": job.status,
        "doc_count": job.doc_count,
        "error": job.error,
        "phase": job.phase,
        "total_documents": job.total_documents,
        "processed_documents": job.processed_documents,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat(),
    }
