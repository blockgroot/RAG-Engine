"""The Workspace Agent: a generic agent for one sub-workspace's own content.

Workspace-within-a-Workspace originally routed every workspace-scoped question
through the same ``PolicyAgent``/``RagPipeline`` used for company policy —
correct for isolation (retrieval was already ``workspace_id``-scoped), but
wrong for *tone*: the grounded prompt is tuned for HR policy Q&A (persona
"company policy assistant", HR-team escalation, "company-specific fact"
language), which reads oddly for a personal meeting-notes or design folder and
has no reason to be limited to meeting notes specifically.

``WorkspaceAgent`` is a second, independent ``RagPipeline`` instance (own
fallback string, own ``PromptProfile`` — see ``app/rag/prompts.py``'s
``WORKSPACE_PROMPT_PROFILE``) reusing the exact same gate/retrieval/tone-guard
machinery, so it's deliberately generic: it says nothing about meetings,
design, or any specific folder type, and works for whatever content a
workspace owner connects. Web-search fallback is off by default (a personal
workspace's content is either present or it isn't — there's no "ask the public
web about this meeting" case the way there is for a real external entity).

Routing: ``app/api/chat.py`` picks this agent over ``PolicyAgent`` whenever a
request carries a ``workspace_id`` — see its module docstring.
"""

from __future__ import annotations

from .rag_pipeline_agent import RagPipelineAgent


class WorkspaceAgent(RagPipelineAgent):
    """Answers questions scoped to one sub-workspace's own connected content."""
