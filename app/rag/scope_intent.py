"""Does answering this question need the WHOLE corpus, or the best few chunks?

Retrieval ranks by similarity and keeps `RAG_TOP_K` chunks. That is right for
"what's our leave policy?" and wrong for "summarise everything discussed here":
measured on a real Slack channel, 5 of 38 chunks reached the model and the
answer came confidently from the single thread phrased most like a summary. A
partial answer is worse than a refusal, because nothing signals it.

The trigger is the QUESTION's intent, not a property of the data. Two rejected
alternatives, and why:

* **A word list** ("overall", "summarise", "everything") misses "what's been
  going on here" and "give me the gist", and fires on "summarise what Sana said
  about Notion" -- which is a SPECIFIC question wearing a summary verb.
* **"Read it whole whenever it fits"** needs no interpretation, but it is not
  intent: it hands a pointed question the entire corpus and stops scaling the
  moment one channel gets busy. It also cannot tell the two "summarise"
  questions above apart, which is the distinction that decides the answer.

So: one small classifier, a CLOSED output set, validated, and fail-open to the
ranked behaviour that shipped. The model picks a label; it never picks the
chunks, the query or the count.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Answerable from the most relevant few chunks. The default, and what every
#: failure degrades to.
SPECIFIC = "specific"
#: Asks about the body of documents AS A WHOLE -- a summary, an overview, the
#: themes, "what is this about". Needs breadth, not the best match.
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

    ``fail_open`` keeps a dead or rate-limited classifier from changing any
    answer: it returns ``SPECIFIC``, which is exactly the ranked retrieval that
    shipped. A breadth read is an IMPROVEMENT on a narrow one, never a
    correctness guarantee, so it must never be the thing an outage takes away.

    Validation is the gate, not the prompt: anything outside the closed set is
    read as ``SPECIFIC`` rather than trusted, so a chatty or hallucinated reply
    costs nothing.
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

    # Substring rather than equality: a small model routinely answers
    # "overview." or "Answer: overview". Checked in a fixed order so a reply
    # naming both cannot be decided by dict ordering -- and the wider read
    # loses that tie, because widening on a maybe is the change with a cost.
    lowered = (reply or "").strip().lower()
    if SPECIFIC in lowered:
        return SPECIFIC
    if OVERVIEW in lowered:
        return OVERVIEW
    logger.info("scope intent: unrecognised reply %r; reading as specific", lowered[:40])
    return SPECIFIC
