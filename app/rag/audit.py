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
