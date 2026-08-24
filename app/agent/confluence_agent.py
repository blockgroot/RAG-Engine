"""The Confluence Agent: answers only from this tenant's ingested Confluence pages.

Same shape and same reasoning as ``NotionAgent``/``DriveAgent`` — Confluence is
another settled-document source, so the split here is purely source
*identity* (never silently blend Confluence content with Notion/Drive/Linear),
not framing. Built via ``build_confluence_agent`` with the pipeline pinned to
``source_provider="confluence"``.
"""

from __future__ import annotations

from .rag_pipeline_agent import RagPipelineAgent


class ConfluenceAgent(RagPipelineAgent):
    """Answers questions from ingested Confluence pages only."""
