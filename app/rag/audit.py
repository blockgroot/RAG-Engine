"""Post-generation groundedness audit.

One bounded call that re-checks a drafted Mode A/B answer against the same
context. A second opinion, never a second author: it can only downgrade to
the fixed fallback. Any failure skips the audit.
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
    Unparseable yields ``grounded=None``: "cannot judge", never ungrounded."""
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
# A token classifier marking unsupported spans with a confidence: no reply to
# parse, no LLM quota. Same contract: only downgrades; any failure is None.

# ModernBERT truncates past 8k tokens, which makes cut-off claims look
# invented, so an over-long context is not audited (~4 chars/token).
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
            timeout=settings.lettuce_timeout,
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
