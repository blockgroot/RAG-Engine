"""The Notion Agent: answers only from this tenant's ingested Notion pages.

Same shape as ``SlackAgent``/``LinearAgent``, but the reason for the split is
different: Notion and Google Drive are the *same kind* of content (settled
written documents, not chat threads or tickets), so there is no framing
concern the way there is for Slack/Linear. The reason this exists is purely
source *identity* — Notion may hold different content than Drive (a company
connecting both is not necessarily using them for the same thing), so an
answer must be traceable to exactly one of them, never silently blended.
Built via ``build_notion_agent`` with the pipeline pinned to
``source_provider="notion"``.
"""

from __future__ import annotations

from .rag_pipeline_agent import RagPipelineAgent


class NotionAgent(RagPipelineAgent):
    """Answers questions from ingested Notion pages only."""
