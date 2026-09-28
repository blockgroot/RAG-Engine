"""Score chunks ingest left unscored, or scored with an older model.

Runs on the external tick, `GUARD_BACKFILL_BATCH` chunks at a time, which is
what lets ingest treat a rate-limited Groq call as "later" rather than "never":
a big first sync overflows the free tier's 15K tokens/min, and those chunks
land here. Idempotent -- a scored row stops matching -- so the tick can call it
as often as it likes.
"""

from __future__ import annotations

from collections import defaultdict

from ..config.settings import GuardSettings
from .base import InjectionGuard
from .factory import build_injection_guard


def backfill_injection_scores(
    store=None, guard: InjectionGuard | None = None, settings: GuardSettings | None = None
) -> int:
    """Score one batch; return how many chunks got a score."""
    settings = settings or GuardSettings.from_env()
    guard = guard or build_injection_guard(settings)
    if guard is None:
        return 0
    if store is None:
        from ..vectorstore import build_vector_store

        store = build_vector_store()
    rows = store.list_unscored_chunks(guard.model, settings.backfill_batch)
    if not rows:
        return 0
    by_doc: dict[str, dict[int, float]] = defaultdict(dict)
    for (document_id, index, _), score in zip(rows, guard.score([c for _, _, c in rows])):
        if score is not None:
            by_doc[document_id][index] = score
    for document_id, scores in by_doc.items():
        store.set_injection_scores(document_id, scores, guard.model)
    return sum(len(s) for s in by_doc.values())
