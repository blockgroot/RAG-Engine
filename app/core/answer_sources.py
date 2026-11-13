"""Answer-provenance labels shared by ``RagResult.source`` and ``AgentResponse.source``, so the spellings never drift."""

from __future__ import annotations

SOURCE_POLICY = "policy"
SOURCE_WORKSPACE = "workspace"
SOURCE_GITHUB = "github"
SOURCE_SLACK = "slack"
SOURCE_LINEAR = "linear"
SOURCE_NOTION = "notion"
SOURCE_GOOGLE = "google"
SOURCE_WEB = "web"
#: An answer built from a file the asker attached to THIS conversation --
#: never retrieved, never indexed, never visible to anyone else.
SOURCE_ATTACHMENT = "attachment"
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
        SOURCE_WEB,
        SOURCE_ATTACHMENT,
        SOURCE_NONE,
    }
)

# Recovery reasons shared by ``RagPipeline`` and ``GitHubAgent`` (re-exported
# from ``app.rag.pipeline``).
RECOVERY_REASON_GATE_MISS = "gate_miss"
RECOVERY_REASON_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
