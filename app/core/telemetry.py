"""Structured observability for the Phase 10 retrieval-retry mechanism.

Uses the standard-library ``logging`` module with fields passed via ``extra=``
— no new dependency, matching the project's dependency-light philosophy
(CLAUDE.md §1). One ``RetryTelemetry`` record is built and emitted per
``RagPipeline._run()`` call, capturing exactly what happened to the retry
mechanism for that question: whether/why a retry fired, what it tried, whether
it helped, and what it cost — enough to answer, from production logs, how often
the retry engages and whether it's worth its cost.

This module is purely observational: nothing here can affect a decision the
pipeline makes. ``RagPipeline`` builds the record's fields as it already
computes them and calls ``.emit()`` once per ``_run()`` call; no retry
trigger, limit, or generation/verification logic lives here.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

logger = logging.getLogger("app.rag.retry")


@dataclass(frozen=True)
class RetryTelemetry:
    """One structured record per ``RagPipeline._run()`` call.

    - ``retry_trigger``            Why (if at all) a retry was attempted this
      call: ``"low_similarity"`` (the confidence gate failed on the first
      attempt) | ``"llm_fallback"`` (the gate passed but evidence
      classification still came back ``"none"``) | ``None`` (no retry was
      attempted — the common, zero-extra-cost path).
    - ``original_query``           The question exactly as retrieval received
      it (before any retry-only rewriting).
    - ``generated_retry_queries``  The alternate phrasings the LLM proposed on
      retry (empty if no retry ran, or the LLM produced none).
    - ``retry_attempt_number``     ``1`` when a retry ran (the mechanism is
      capped at exactly one bounded, non-recursive attempt); ``0`` when no
      retry ran this call.
    - ``retry_success``            ``True`` iff a retry ran AND it actually
      changed the outcome (cleared the gate that had failed, or flipped a
      ``"none"`` classification to something else).
    - ``retry_latency_ms``         Wall-clock time spent inside the retry
      attempt (query-understanding LLM call + re-retrieval); ``0.0`` if no
      retry ran.
    - ``top_score_before_retry``   The gate/confidence score from the FIRST
      attempt (``None`` if nothing was retrieved at all).
    - ``top_score_after_retry``    The gate/confidence score after the retry
      (``None`` if no retry ran, or it also retrieved nothing).
    - ``retrieval_improved``       ``True`` iff a retry ran and
      ``top_score_after_retry`` is strictly greater than
      ``top_score_before_retry``.
    - ``final_answer_source``      Where the RETURNED result actually came
      from: ``"first_attempt"`` | ``"retry"`` | ``"fallback"``.
    """

    retry_trigger: str | None
    original_query: str
    generated_retry_queries: list[str] = field(default_factory=list)
    retry_attempt_number: int = 0
    retry_success: bool = False
    retry_latency_ms: float = 0.0
    top_score_before_retry: float | None = None
    top_score_after_retry: float | None = None
    retrieval_improved: bool = False
    final_answer_source: str = "first_attempt"

    def emit(self) -> None:
        """Log this record as one structured line via the stdlib logger.

        The human-readable message carries the high-signal fields inline (so
        it's readable in a plain-text log stream); the full record is also
        attached under ``extra={"rag_retry": {...}}`` so a JSON/structured log
        processor can index every field without parsing the message text.
        """
        logger.info(
            "rag_retry trigger=%s attempt=%s success=%s improved=%s "
            "source=%s latency_ms=%.1f score_before=%s score_after=%s",
            self.retry_trigger,
            self.retry_attempt_number,
            self.retry_success,
            self.retrieval_improved,
            self.final_answer_source,
            self.retry_latency_ms,
            self.top_score_before_retry,
            self.top_score_after_retry,
            extra={"rag_retry": asdict(self)},
        )
