"""The Agent contract: a question in, a structured, source-attributed answer out.

Phase 7 formalizes what has, until now, lived as loose logic inside the CLI
scripts (``ask.py`` / ``chat.py``) into a single reusable unit. An *agent* takes a
natural-language question for a specific tenant (and, optionally, a conversation it
belongs to) and returns a structured ``AgentResponse``.

The interface is deliberately **small and generic** — it says nothing about Notion,
policies, retrieval, gates, or web search. A future GitHub agent (not built in this
phase) will implement the *same* contract, so nothing source-specific may leak into
it. The one concrete implementation today is ``PolicyAgent`` (see
``policy_agent.py``), which composes the existing Phase 3–6 RAG pipeline.

This mirrors the ``rag/`` package's stance: an agent is an *orchestrator*, but
unlike the RAG pipeline there genuinely is a second backend coming (GitHub), so —
per CLAUDE.md §3 — this package *does* get a ``base.py`` abstract contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Citation:
    """A single piece of evidence an answer was grounded on.

    Kept generic so any agent can populate it: a policy agent maps one retrieved
    chunk to a citation; a future GitHub agent might map a file/section. ``score``
    is the retrieval similarity when meaningful, else ``None``.

    - ``content``    the source text the answer drew on.
    - ``reference``  a stable, human-meaningful locator for the source. For the
      policy agent this is ``"<document_id>#<chunk_index>"``; other agents may use
      a URL or path.
    - ``score``      retrieval similarity in ``[0, 1]`` when available.
    """

    content: str
    reference: str
    score: float | None = None


@dataclass(frozen=True)
class AgentResponse:
    """The structured outcome of answering one question for one tenant.

    - ``answer``     the text to show the user (a real answer, or a fixed "I don't
      know" fallback when nothing could be grounded).
    - ``grounded``   ``True`` when a real answer was produced (from source docs OR
      web search); ``False`` for the fallback. Callers branch on this bool.
    - ``source``     provenance of the answer: ``"policy"`` (internal docs),
      ``"web"`` (web-search fallback), or ``"none"`` (fixed fallback / refusal).
    - ``citations``  the evidence the answer was grounded on (empty for web answers
      and refusals).
    - ``resolved_question``  the standalone question actually used after any
      conversation-aware rewriting (``None`` outside a conversation). Exposed so a
      follow-up's rewrite is observable/testable.
    - ``top_score``  best retrieval similarity seen (``None`` if nothing retrieved).
      A diagnostic for logging / gate analysis; not part of the user-facing answer.
    """

    answer: str
    grounded: bool
    source: str = "policy"
    citations: list[Citation] = field(default_factory=list)
    resolved_question: str | None = None
    top_score: float | None = None


class Agent(ABC):
    """A tenant-scoped question-answering agent.

    One method: given a ``question``, the ``org_id`` it is asked on behalf of, and
    an optional ``conversation_id`` for multi-turn context, return a structured
    ``AgentResponse``. Tenant isolation is a hard contract — an agent must only ever
    use data belonging to ``org_id``.
    """

    @abstractmethod
    def answer(
        self, question: str, org_id: str, *, conversation_id: str | None = None
    ) -> AgentResponse:
        """Answer ``question`` for ``org_id`` (optionally within a conversation)."""
        raise NotImplementedError
