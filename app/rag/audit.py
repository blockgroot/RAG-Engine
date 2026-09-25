"""Post-generation groundedness audit — the validation-layer gap.

A single bounded LLM call that re-checks an already-drafted Mode A/B answer
against the same retrieved context, looking for a concrete claim the context
does not support. This is a second opinion, never a second author: it can
only downgrade an answer to the fixed fallback, never edit or extend one.

Mirrors the existing bounded-extra-call shapes already in this pipeline
(retrieval recovery, tone-compliance retry): budget-gated, one attempt, and
any failure (LLM error, unparseable response) degrades to "skip the audit"
rather than blocking or corrupting the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_VERDICT_RE = re.compile(r"VERDICT:\s*(GROUNDED|UNGROUNDED)", re.IGNORECASE)
_REASON_RE = re.compile(r"REASON:\s*(.+)", re.IGNORECASE)


@dataclass(frozen=True)
class AuditVerdict:
    """Parsed result of one audit call. ``grounded is None`` means unparseable."""

    grounded: bool | None
    reason: str | None


def parse_audit_verdict(raw: str) -> AuditVerdict:
    """Parse the auditor's ``VERDICT: ...`` / ``REASON: ...`` reply.

    Unparseable output (a different aux model, an empty response, a refusal)
    yields ``grounded=None`` — the caller must treat that as "cannot judge"
    and leave the answer untouched, never as evidence of ungroundedness.
    """
    m = _VERDICT_RE.search(raw)
    if not m:
        return AuditVerdict(grounded=None, reason=None)
    grounded = m.group(1).upper() == "GROUNDED"
    r = _REASON_RE.search(raw)
    reason = r.group(1).strip() if r else None
    if reason in (None, "", "(none)"):
        reason = None
    return AuditVerdict(grounded=grounded, reason=reason)


# -- LettuceDetect backend (RAG_AUDIT_BACKEND=lettuce) --------------------------
#
# A token classifier, not a chatbot: it marks the spans of the answer the
# context does not support, each with a confidence. No prompt to parse, so no
# `grounded=None` from a reply worded oddly, and no LLM quota spent. Same
# contract as the LLM path: it can only downgrade, and any failure is None
# ("skip the audit"), never evidence of ungroundedness.

# ModernBERT reads 8k tokens and TRUNCATES beyond that. A truncated context
# makes every claim sourced from the cut-off part look invented, so an
# over-long context is not audited at all rather than audited wrongly. ~4
# chars/token leaves room for the question and answer.
LETTUCE_MAX_CONTEXT_CHARS = 24_000


def lettuce_score(
    settings, question: str, contexts: list[str], answer: str
) -> tuple[float, str | None] | None:
    """Highest hallucinated-span confidence (0.0 = nothing flagged) and that span.

    ``None`` when the check cannot run: context over budget, endpoint down,
    malformed reply. ``settings`` is an ``AuditSettings``.
    """
    import httpx

    from ..security.untrusted import scrub_untrusted_text

    # Check against what the generator actually saw — the scrubbed text.
    context = [c for c in (scrub_untrusted_text(x) for x in contexts) if c]
    if not context or sum(len(c) for c in context) > LETTUCE_MAX_CONTEXT_CHARS:
        return None
    headers = {"Authorization": f"Bearer {settings.lettuce_token}"} if settings.lettuce_token else {}
    try:
        response = httpx.post(
            settings.lettuce_url.rstrip("/") + "/check",
            json={"context": context, "question": question, "answer": answer},
            headers=headers,
            timeout=settings.timeout,
        )
        response.raise_for_status()
        spans = response.json()["spans"]
        best = max(spans, key=lambda s: float(s["confidence"]), default=None)
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    if best is None:
        return 0.0, None
    return float(best["confidence"]), str(best.get("text") or "")[:160] or None


def lettuce_verdict(settings, question: str, contexts: list[str], answer: str) -> AuditVerdict | None:
    """``lettuce_score`` against ``settings.lettuce_threshold``; ``None`` = skip."""
    scored = lettuce_score(settings, question, contexts, answer)
    if scored is None:
        return None
    confidence, span = scored
    if confidence < settings.lettuce_threshold:
        return AuditVerdict(grounded=True, reason=None)
    return AuditVerdict(grounded=False, reason=f"Unsupported ({confidence:.2f}): {span}" if span else None)


# -- Jev backend (RAG_AUDIT_BACKEND=jev) -----------------------------------------
#
# TypeSafe's hosted "System One" model: no text out, a probability per typed
# question. One request per answer: the state is the passages plus the answer,
# and each answer SENTENCE is its own yes/no, all scored in parallel against
# that state. Per-sentence, not one question for the whole answer, so a single
# invented sentence in a long answer cannot be averaged away — and so the
# reason can name it. Nothing to deploy; tenant chunks DO leave for TypeSafe.

# Two ways in, same model, slightly different wire shapes:
# - vercel (default): Vercel AI Gateway's /v1/evaluate. Its monthly $5 free
#   credit covers Jev (~$0.0001/check), and it can REQUIRE zero data retention
#   per request — pinned to TypeSafe's own endpoint, because the gateway's other
#   Jev provider (DigitalOcean) is not ZDR. A request that cannot be served
#   under ZDR fails, and a failure skips the audit: never a silent fallback to
#   a retaining provider.
# - typesafe: TypeSafe's own API. No ZDR (enterprise-only there).
# ponytail: both use floating aliases; pin a version once a threshold is
# calibrated on our labels, since a model update moves every probability.
JEV_GATEWAYS = {
    "vercel": ("https://ai-gateway.vercel.sh/v1/evaluate", "typesafe-ai/jev", "boolean", "probability"),
    "typesafe": ("https://api.typesafe.ai/v1/systemone", "jev-latest", "noul", "noul"),
}
# Jev's request budget is ~32k tokens; stay well under it rather than let the
# service reject or truncate the state.
JEV_MAX_CONTEXT_CHARS = 90_000
JEV_MAX_SENTENCES = 20


def split_sentences(text: str, limit: int = JEV_MAX_SENTENCES) -> list[str]:
    """Crude sentence split: enough to give each claim its own question.

    Over ``limit`` sentences the TAIL is merged into the last question rather
    than dropped — an unchecked sentence would be an unaudited claim.
    """
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if len(p.strip()) > 3]
    if len(parts) > limit:
        parts = parts[: limit - 1] + [" ".join(parts[limit - 1 :])]
    return parts or ([text.strip()] if text.strip() else [])


def jev_score(
    settings, question: str, contexts: list[str], answer: str
) -> tuple[float, str | None] | None:
    """Lowest per-sentence P(supported) and that sentence; ``None`` = cannot check."""
    import httpx

    from ..security.untrusted import scrub_untrusted_text

    context = [c for c in (scrub_untrusted_text(x) for x in contexts) if c]
    sentences = split_sentences(answer)
    if not context or not sentences or sum(len(c) for c in context) > JEV_MAX_CONTEXT_CHARS:
        return None
    url, model, qtype, field = JEV_GATEWAYS[settings.jev_gateway]
    questions = {
        f"s{i}": {
            "type": qtype,
            "instructions": (
                "Is this sentence from the answer fully supported by the passages? "
                f"Sentence: {s}"
            ),
        }
        for i, s in enumerate(sentences)
    }
    body = {
        "model": model,
        "state": {"question": question, "passages": context, "answer": answer},
        "questions": questions,
    }
    if settings.jev_gateway == "vercel":
        body["providerOptions"] = {"gateway": {"zeroDataRetention": True, "only": ["typesafe-ai"]}}
    try:
        response = httpx.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {settings.jev_api_key}"},
            timeout=settings.timeout,
        )
        response.raise_for_status()
        answers = response.json()["answers"]
        probs = [float(answers[f"s{i}"][field]) for i in range(len(sentences))]
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
    worst = min(range(len(probs)), key=probs.__getitem__)
    return probs[worst], sentences[worst][:160]


def jev_verdict(settings, question: str, contexts: list[str], answer: str) -> AuditVerdict | None:
    """``jev_score`` against ``settings.jev_threshold``; ``None`` = skip."""
    scored = jev_score(settings, question, contexts, answer)
    if scored is None:
        return None
    supported, sentence = scored
    if supported >= settings.jev_threshold:
        return AuditVerdict(grounded=True, reason=None)
    return AuditVerdict(grounded=False, reason=f"Unsupported ({supported:.2f}): {sentence}")
