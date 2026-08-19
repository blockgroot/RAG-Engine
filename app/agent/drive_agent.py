"""The Drive Agent: answers only from this tenant's ingested Google Drive documents.

See ``notion_agent.py`` for why Notion and Drive are split despite both being
document-shaped content: the split is about source identity, not tone. Built
via ``build_drive_agent`` with the pipeline pinned to ``source_provider="google"``
(matching the ``documents.source_provider`` value the Google Drive adapter
writes — see ``app/sources/google_drive.py``).
"""

from __future__ import annotations

from .rag_pipeline_agent import RagPipelineAgent


class DriveAgent(RagPipelineAgent):
    """Answers questions from ingested Google Drive documents only."""
