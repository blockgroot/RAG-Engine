"""Canonical answer-provenance labels, shared by ``RagResult``/``AgentResponse``.

A tiny utility module, not an interface + factory package (there is nothing to
swap — just a vocabulary that ``app/rag/`` and ``app/agent/`` must agree on).
Both ``RagResult.source`` and ``AgentResponse.source`` use these same string
values (``rag_pipeline_agent.py`` passes ``RagResult.source`` straight through),
so a single spelling here is what keeps them from drifting apart.
"""

from __future__ import annotations

SOURCE_POLICY = "policy"
SOURCE_WORKSPACE = "workspace"
SOURCE_GITHUB = "github"
SOURCE_SLACK = "slack"
SOURCE_LINEAR = "linear"
SOURCE_NOTION = "notion"
SOURCE_GOOGLE = "google"
SOURCE_CONFLUENCE = "confluence"
SOURCE_WEB = "web"
SOURCE_NONE = "none"

# Every value an agent may report as `source`/`final_answer_source`.
ANSWER_SOURCES = frozenset(
    {
        SOURCE_POLICY,
        SOURCE_WORKSPACE,
        SOURCE_GITHUB,
        SOURCE_SLACK,
        SOURCE_LINEAR,
        SOURCE_NOTION,
        SOURCE_GOOGLE,
        SOURCE_CONFLUENCE,
        SOURCE_WEB,
        SOURCE_NONE,
    }
)

# Architectural recovery reasons (Retrieval Discovery Gap) — shared between
# ``RagPipeline`` and ``GitHubAgent`` so the two never spell "insufficient
# evidence" differently. Re-exported from ``app.rag.pipeline`` for callers
# (and tests) that import them from there.
RECOVERY_REASON_GATE_MISS = "gate_miss"
RECOVERY_REASON_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
