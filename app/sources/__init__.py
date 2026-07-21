"""External content sources (Notion now; Drive/GitHub/Slack later).

Public API:
    from app.sources import build_source_adapter
    adapter = build_source_adapter("notion")
    for ref in adapter.list_documents():
        doc = adapter.fetch_document(ref.external_id)
"""

from .base import SourceAdapter, SourceRef, SourceDocument
from .notion import NotionAdapter
from .factory import build_source_adapter

__all__ = [
    "SourceAdapter",
    "SourceRef",
    "SourceDocument",
    "NotionAdapter",
    "build_source_adapter",
]
