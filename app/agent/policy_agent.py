"""The Policy Agent: the company-policy Q&A agent, as a formal unit.

Phase 7 extracts the retrieve → gate → generate → memory → web-search-fallback
behavior that previously lived scattered across the CLI scripts into one place.
The logic is **not rewritten** — it still lives, exactly as before, in
``RagPipeline`` (Phases 3–6). ``PolicyAgent`` is a thin adapter (see
``RagPipelineAgent`` for the shared ``RagResult`` -> ``AgentResponse`` mapping
also used by ``WorkspaceAgent``) that composes an already-built ``RagPipeline``
built with the default (company-policy) ``PromptProfile`` — so the one place
callers (scripts, the eval harness, the HTTP layer) reach for policy answers is
this class, while the gate, prompt, and every test outcome stay byte-for-byte
unchanged, because the pipeline underneath is untouched.

Prefer building via ``factory.build_policy_agent``.
"""

from __future__ import annotations

from .rag_pipeline_agent import RagPipelineAgent


class PolicyAgent(RagPipelineAgent):
    """Answers policy questions for a tenant by delegating to a ``RagPipeline``."""
