"""The Linear Agent: answers only from this tenant's ingested Linear issues.

Same shape as ``SlackAgent`` and for the same two reasons (see its docstring):
a Notion/Drive-populated Policies tab must not silently blend in engineering
tickets (relevance), and a ticket comment must not be presented with the same
authority as a handbook sentence (framing — its own ``LINEAR_PROMPT_PROFILE``
and ``source`` label handle that). Built via ``build_linear_agent`` with the
pipeline pinned to ``source_provider="linear"``.

Unlike Slack, issue content doesn't need a recency-based recap retry: an
issue's title/description/comments are already a coherent, self-contained
unit (unlike a raw chat log), so plain similarity retrieval is the right tool
without a second attempt. Add one later if this corpus grows large enough
that "what's the status of X" style questions start missing, same trigger
that justified it for Slack.
"""

from __future__ import annotations

from .rag_pipeline_agent import RagPipelineAgent


class LinearAgent(RagPipelineAgent):
    """Answers questions from ingested Linear issues only."""
