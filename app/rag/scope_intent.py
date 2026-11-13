"""Does answering this question need the WHOLE corpus, or the best few chunks?

Ranked retrieval answers "summarise everything here" from a handful of
chunks, a partial answer that looks complete. The trigger is the
question's intent: a word list misfires ("summarise what Sana said about
X" is specific), and reading whole whenever it fits is not intent.

One small classifier with a CLOSED output set, validated, failing open to
ranked retrieval. It picks a label, never chunks, query or count.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Answerable from the most relevant few chunks; what every failure degrades to.
SPECIFIC = "specific"
#: About the documents AS A WHOLE (summary, overview, themes): needs breadth.
OVERVIEW = "overview"

_INTENTS = frozenset({SPECIFIC, OVERVIEW})

MAX_TOKENS = 8

_PROMPT = """Classify what a search over a document collection must return to answer this question.

Answer with exactly one word:

specific - the answer lives in a few particular documents. Facts, decisions,
           definitions, what one person said, what happened to one thing.
           Includes questions that say "summarise" but name a single topic,
           document or person.
overview - the answer requires reading the whole collection. Summaries of
           everything, overall themes, "what is this about", "catch me up",
           "what have we been discussing", the goals or direction of the
           collection as a whole.

Question: {question}

One word:"""


def classify_scope_intent(question: str, *, llm=None, fail_open: bool = True) -> str:
    """Return ``SPECIFIC`` or ``OVERVIEW`` for ``question``.

    ``fail_open``: an outage returns ``SPECIFIC`` (ranked retrieval). Anything
    outside the closed set reads as ``SPECIFIC``.
    """
    text = (question or "").strip()
    if not text:
        return SPECIFIC

    if llm is None:
        from ..llm.factory import build_llm_provider

        llm = build_llm_provider()

    try:
        reply = llm.generate(_PROMPT.format(question=text), max_tokens=MAX_TOKENS)
    except Exception:  # noqa: BLE001
        logger.warning("scope intent classification failed", exc_info=True)
        if not fail_open:
            raise
        return SPECIFIC

    # Substring, not equality ("Answer: overview."). Fixed order, and the wider
    # read loses a tie: widening on a maybe is the costly change.
    lowered = (reply or "").strip().lower()
    if SPECIFIC in lowered:
        return SPECIFIC
    if OVERVIEW in lowered:
        return OVERVIEW
    logger.info("scope intent: unrecognised reply %r; reading as specific", lowered[:40])
    return SPECIFIC
