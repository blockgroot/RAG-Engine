"""Deterministic, embedding-based answer verification (Phase 10).

Why deterministic, not an LLM judge: faithfulness checking ("is this sentence
actually supported by this evidence?") is exactly the kind of per-sentence
semantic-similarity comparison an embedding model is already good at, and this
pipeline already has one loaded (BGE-M3, for retrieval) — so verification adds
**zero LLM calls and zero new model downloads**. An LLM-judge verifier would cost
one more model call per answer (or per sentence) for a check that, in the common
case, a similarity comparison already answers well enough to catch fabrication.

Honest limitation: this is a semantic-OVERLAP heuristic, not true logical
entailment. It can miss a negation flip ("requires manager approval" vs "does NOT
require manager approval" embed very similarly) or a numeric-detail swap that
reuses the right words with the wrong number. If false negatives/positives prove
too frequent in practice, the documented upgrade path is a dedicated NLI
cross-encoder model (e.g. a `cross-encoder/nli-*` model via the same
``sentence-transformers`` ``CrossEncoder`` class already used for reranking)
behind this SAME ``Verifier`` interface — no pipeline change needed, only a new
``Verifier`` impl + a factory branch. That tradeoff (cheaper/faster now, an
available precision upgrade later) is the same "start conservative, validate
against real behavior" discipline already applied to the 0.35 gate and the 0.72
reuse threshold (see CLAUDE.md §4).
"""

from __future__ import annotations

import re

import numpy as np

from ..embeddings.base import EmbeddingProvider
from .base import ClaimVerdict, Verifier, VerificationResult

# Empirically calibrated (BGE-M3, small sample — see CLAUDE.md §4): sentences
# directly supported by real evidence scored 0.75-0.86 cosine similarity;
# fabricated claims sharing the evidence's formal register (but stating
# something absent) scored 0.51-0.57. 0.65 sits in that gap — above every
# observed fabrication, below every observed support. A starting point, like
# the 0.35 retrieval gate and 0.72 reuse threshold; validate against logged
# outcomes, don't lower it to force fewer regenerations.
DEFAULT_SIMILARITY_THRESHOLD = 0.65

# Markdown headings ("#".."######") are section TITLES, not factual claims —
# skipped entirely, never verified. Bullets/numbered items keep their content
# (that's often exactly where a real claim lives) but lose the list marker.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+")
_LIST_MARKER_RE = re.compile(r"^\s{0,3}([-*+]\s+|\d+[.)]\s+)")
_CITATION_RE = re.compile(r"\[\d+\]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# Fragments shorter than this are treated as formatting/noise, not claims worth
# verifying (a lone "Yes.", a stray fragment, etc.).
_MIN_CLAIM_LENGTH = 15

# Hedging / meta-statements about what the evidence does NOT say — exactly the
# honest language the implicit/partial prompt (build_classified_grounded_prompt)
# instructs the model to use ("the policy does not explicitly state...", "this
# is not mentioned...") — are exempt from the similarity check. Checking such a
# sentence against evidence similarity is nonsensical by construction: a
# statement about an absence naturally has low textual similarity to text that
# doesn't contain the absent thing, so verifying it would penalize the pipeline
# for being appropriately honest instead of catching an actual fabrication.
_HEDGE_RE = re.compile(
    r"\b(does not|do not|doesn't|don't|is not|are not|isn't|aren't|"
    r"cannot|can't|not)\b"
    r"[^.!?]{0,50}\b(mention|state|stated|specify|specified|address|"
    r"addressed|say|said|cover|covered|indicate|indicated|explicitly|"
    r"explicit|clear|clearly|apply|applicable|include|included|list|"
    r"listed|permit|permitted|eligible|reimburs\w*|allow\w*|require\w*|"
    r"applicable)\b",
    re.IGNORECASE,
)


def _split_into_claims(answer: str) -> list[str]:
    """Split a (possibly Markdown) answer into sentence-level factual claims.

    Excludes markdown section headings and hedging/meta-statements about what
    the evidence does NOT cover (see ``_HEDGE_RE``) — neither is a factual claim
    that should be checked for positive evidentiary support.
    """
    claims: list[str] = []
    for line in answer.splitlines():
        if _HEADING_RE.match(line):
            continue  # a section title, not a claim to verify
        line = _LIST_MARKER_RE.sub("", line).strip()
        if not line:
            continue
        line = _CITATION_RE.sub("", line).strip()
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            sentence = sentence.strip(" -*")
            if len(sentence) < _MIN_CLAIM_LENGTH:
                continue
            if _HEDGE_RE.search(sentence):
                continue
            claims.append(sentence)
    return claims


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


class EmbeddingSimilarityVerifier(Verifier):
    """Verifies an answer's sentences via cosine similarity to retrieved evidence.

    Each sentence of the drafted answer is embedded and compared against every
    evidence chunk; the BEST match determines support. A sentence with no
    evidence chunk clearing ``similarity_threshold`` is flagged unsupported.
    """

    def __init__(
        self,
        embedder: EmbeddingProvider,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> None:
        self._embedder = embedder
        self._threshold = similarity_threshold

    def verify(self, answer: str, evidence: list[str]) -> VerificationResult:
        claims = _split_into_claims(answer)
        if not claims or not evidence:
            # Nothing to check (e.g. a one-line answer with no verifiable
            # sentences, or no evidence at all) -> nothing to flag.
            return VerificationResult(supported=True, verdicts=[], unsupported=[])

        # EmbeddingProviderError propagates as-is (a subclass of ProviderError) —
        # callers treat a raised error as "verification unavailable", never a
        # silent pass, per the Verifier contract.
        claim_vecs = np.asarray(self._embedder.embed(claims), dtype=np.float32)
        evidence_vecs = np.asarray(self._embedder.embed(evidence), dtype=np.float32)

        verdicts: list[ClaimVerdict] = []
        for sentence, vec in zip(claims, claim_vecs):
            best = max(
                (_cosine(vec, ev_vec) for ev_vec in evidence_vecs), default=0.0
            )
            verdicts.append(
                ClaimVerdict(
                    sentence=sentence, supported=best >= self._threshold, best_score=best
                )
            )

        unsupported = [v.sentence for v in verdicts if not v.supported]
        return VerificationResult(
            supported=not unsupported, verdicts=verdicts, unsupported=unsupported
        )
