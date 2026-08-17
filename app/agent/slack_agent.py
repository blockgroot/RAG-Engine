"""The Slack Agent: answers only from this tenant's ingested Slack threads.

Slack is ingested like Notion/Drive (fetch → chunk → embed → store), so unlike
``GitHubAgent`` this is a genuine retrieval agent and reuses ``RagPipelineAgent``
exactly as ``PolicyAgent``/``WorkspaceAgent`` do. What makes it a separate agent
rather than another caller of ``PolicyAgent`` is two things the policy pipeline
cannot express at once:

1. **Corpus.** Its pipeline is pinned to ``source_provider="slack"``, so
   retrieval reaches only chunks whose document came from Slack. A tab labelled
   "Slack" that answered from a Notion page would be lying about where the
   answer came from — and the user cannot tell the difference from the answer
   text alone. This is a *relevance* boundary, not an access one: everything it
   can reach was already inside the caller's ``org_id``/``workspace_id``, which
   remain the only isolation guarantees (see ``app/vectorstore/base.py``).

2. **Framing.** Chat threads are not documents. People in a Slack thread think
   out loud, disagree, and change their minds, so a passing "let's just do X"
   is not the same kind of claim as a sentence in a handbook — and answering
   with the policy persona would launder one into the other. Hence its own
   ``SLACK_PROMPT_PROFILE`` (see ``app/rag/prompts.py``) and its own
   ``source`` label, so a caller can always tell a Slack-derived answer apart.

Everything else — the confidence gate, the strict grounded prompt, reranking,
recovery, memory — is the unchanged shared machinery. This agent adds no
answering logic of its own, exactly like ``PolicyAgent`` (see CLAUDE.md §4:
"PolicyAgent must not add behavior — it's an adapter").
"""

from __future__ import annotations

from .rag_pipeline_agent import RagPipelineAgent


class SlackAgent(RagPipelineAgent):
    """Answers questions from ingested Slack threads only."""
