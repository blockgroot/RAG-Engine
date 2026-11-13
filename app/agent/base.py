"""The Agent contract: a question in, a structured, source-attributed answer out.

Small and generic: nothing source-specific. Implementations: RAG agents
over ``RagPipeline`` (``RagPipelineAgent``: policy, workspace, Slack and
the other connectors), ``GitHubAgent`` (live reads) and ``InsightsAgent``
(charts from SQL). There is one agent per source, hence this ``base.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..core.answer_sources import SOURCE_POLICY
from ..vectorstore.base import Viewer


@dataclass(frozen=True)
class Citation:
    """A single piece of evidence an answer was grounded on.

    ``reference`` is a human-meaningful locator (``"<title> · excerpt N"``, or
    ``owner/repo#readme``). ``score`` is retrieval similarity when meaningful.
    """

    content: str
    reference: str
    score: float | None = None
    #: http(s) address from the source itself, never a URL the model wrote.
    url: str | None = None


@dataclass(frozen=True)
class AgentResponse:
    """The structured outcome of answering one question for one tenant.

    ``grounded`` is False only for the fixed fallback; callers branch on it.
    ``source``: ``policy``, ``workspace``, ``github`` (live read), ``web`` or
    ``none``. ``citations`` is empty for web answers and refusals. The rest
    (``resolved_question``, scores, reuse, recovery, mode, tone) are
    diagnostics.
    """

    answer: str
    grounded: bool
    source: str = SOURCE_POLICY
    citations: list[Citation] = field(default_factory=list)
    resolved_question: str | None = None
    top_score: float | None = None
    retrieval_reused: bool = False
    recovery_used: bool = False
    recovery_reason: str | None = None
    recovery_queries: list[str] = field(default_factory=list)
    retrieval_improved: bool = False
    top_score_before: float | None = None
    top_score_after: float | None = None
    latency_ms: float | None = None
    response_mode: str | None = None
    tone_retry_used: bool = False
    question_tone: str | None = None
    #: "Matched but not shared with you", carried so `/chat` never logs a
    #: withheld document as a documentation gap (without matching text).
    access_restricted: bool = False
    #: Spread over several documents: Ask offers Deep analysis.
    wide_read: bool = False
    #: Connectors read LIVE for this answer, and whether a live read withheld
    #: the item behind a refusal (kept out of the gap log).
    live_sources: list[dict] = field(default_factory=list)
    live_withheld: bool = False
    #: The documents behind ``[n]`` markers (``rag/cite.py``).
    cited: list[dict] = field(default_factory=list)
    #: Set only by InsightsAgent. SQL points + shape; never model-drawn SVG.
    chart: dict | None = None
    chart_period: str | None = None


class Agent(ABC):
    """A tenant-scoped question-answering agent. It must only ever use data
    belonging to ``org_id``."""

    @abstractmethod
    def answer(
        self,
        question: str,
        org_id: str,
        *,
        conversation_id: str | None = None,
        workspace_id: str | None = None,
        viewer: "Viewer | None" = None,
    ) -> AgentResponse:
        """Answer ``question`` for ``org_id`` (optionally within a conversation).

        ``viewer``: WHO is asking, so retrieval drops documents not shared with
        them. ``None`` is eval/CLI only. Corpus-less agents (GitHub, Insights)
        may ignore it. ``workspace_id`` scopes to that workspace only; an agent
        must never widen it to org-wide on a miss.
        """
        raise NotImplementedError
