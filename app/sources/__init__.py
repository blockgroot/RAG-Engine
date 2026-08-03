"""External content sources (Notion now; Drive/GitHub/Slack later).

Public API:
    from app.sources import build_source_adapter
    adapter = build_source_adapter("notion")
    for ref in adapter.list_documents():
        doc = adapter.fetch_document(ref.external_id)
"""

from .base import SourceAdapter, SourceRef, SourceDocument
from .notion import NotionAdapter
from .google_drive import GoogleDriveAdapter
from .factory import build_source_adapter
from .google_drive_utils import (
    extract_drive_folder_id,
    search_drive_folders,
    validate_drive_folder,
)

__all__ = [
    "SourceAdapter",
    "SourceRef",
    "SourceDocument",
    "NotionAdapter",
    "GoogleDriveAdapter",
    "build_source_adapter",
    "extract_drive_folder_id",
    "search_drive_folders",
    "validate_drive_folder",
]
