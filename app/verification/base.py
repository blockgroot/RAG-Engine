"""The answer-verification contract (Phase 10).

After a grounded answer is generated (explicit/implicit/partial classifications —
see ``app/rag/prompts.py``), a ``Verifier`` checks that every factual sentence in
the drafted answer is actually supported by the retrieved evidence chunks, so an
unsupported claim never reaches the user unexamined. This sits behind an
interface + factory like every other capability, so the checking technique can be
swapped (e.g. a dedicated NLI cross-encoder, or an LLM-judge verifier) without
touching the pipeline that calls it.

The default implementation (``embedding_similarity.py``) is deliberately
deterministic — no LLM call — reusing the embedding model the pipeline already
has loaded. See that module's docstring for why, and its documented limitations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ClaimVerdict:
    """The verification outcome for one sentence of a drafted answer."""

    sentence: str
    supported: bool
    best_score: float  # similarity/entailment score against the best-matching evidence


@dataclass(frozen=True)
class VerificationResult:
    """The outcome of verifying a whole answer against retrieved evidence.

    - ``supported``    ``True`` iff every checked sentence was supported.
    - ``verdicts``      per-sentence detail, for logging/debugging.
    - ``unsupported``   convenience: just the flagged sentences, so a caller can
      hand them to a stricter-regeneration prompt without re-deriving them.
    """

    supported: bool
    verdicts: list[ClaimVerdict] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)


class Verifier(ABC):
    """Abstract post-generation faithfulness checker."""

    @abstractmethod
    def verify(self, answer: str, evidence: list[str]) -> VerificationResult:
        """Check whether ``answer``'s factual content is supported by ``evidence``.

        Must raise ``core.exceptions.ProviderError`` (or a subclass) on failure —
        callers treat a raised error as "verification unavailable", never as a
        silent pass.
        """
        raise NotImplementedError
