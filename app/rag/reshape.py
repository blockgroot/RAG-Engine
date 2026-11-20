"""Reshape the last answer ("shorter", "as a table", "in Hindi") without searching.

The question check says the follow-up only changes the FORM of the last answer
(``AskIntent.reshape``). Then the facts are already on the turn, so searching
again would only cost time. Two rules keep that safe:

* The last turn is reshaped only while every document it cited is still in
  this scope, open to this asker, and not re-synced since the turn. Anything
  else (no citations, a chart, an id that is not a document) searches as usual.
* The rewrite adds nothing: its links must be in the old answer, its markers
  must be old citations, and it passes the same canary and moderation checks
  as any answer.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

from ..db.connection import get_connection
from ..security.visibility import viewer_clause

logger = logging.getLogger(__name__)

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def reshapeable(turn, *, org_id: str, workspace_id: str | None, viewer) -> bool:
    """True when ``turn`` (a ``ConversationTurnRow``) may be reshaped for this
    asker now. Never raises: any doubt is False, and the question is searched."""
    if turn is None or viewer is None or turn.chart or not turn.cited:
        return False
    ids = {c.get("document_id") for c in turn.cited if isinstance(c, dict)}
    if not ids or len(ids) != len(turn.cited) or not all(
        isinstance(i, str) and _UUID.match(i) for i in ids
    ):
        return False  # a live read or an unknown entry: not provably a document
    try:
        return readable_unchanged(ids, org_id=org_id, workspace_id=workspace_id,
                                  viewer=viewer, since=turn.created_at) == ids
    except Exception:  # noqa: BLE001 - a shortcut must never cost an answer
        logger.warning("reshape check failed; searching instead", exc_info=True)
        return False


def readable_unchanged(ids: set[str], *, org_id: str, workspace_id: str | None,
                       viewer, since) -> set[str]:
    """The ``ids`` this viewer may read in this scope whose chunks were all
    written by ``since`` (a re-sync rewrites every chunk of a document)."""
    viewer_sql, viewer_params = viewer_clause(viewer, "d")
    with get_connection(org_id=org_id) as conn:
        rows = conn.execute(
            f"""
            SELECT d.id::text FROM documents d
            WHERE d.org_id = %s::uuid
              AND d.workspace_id IS NOT DISTINCT FROM %s::uuid
              AND d.id = ANY(%s::uuid[])
              AND NOT EXISTS (
                  SELECT 1 FROM chunks c
                  WHERE c.org_id = d.org_id AND c.document_id = d.id
                    AND c.created_at > %s)
              {viewer_sql}
            """,
            (org_id, workspace_id, sorted(ids), since, *viewer_params),
        ).fetchall()
    return {r[0] for r in rows}


def reshape(request: str, turn, *, org_id: str, llm=None) -> tuple[str, list[dict]] | None:
    """``(answer, cited)`` rewritten from ``turn``, or None to search instead."""
    from ..config.settings import RagSettings, SecuritySettings
    from ..guard.moderation import answer_is_unsafe
    from ..security.links import enforce_link_provenance
    from ..security.untrusted import leaks_canary
    from .cite import link_citations, strip_citations
    from .prompts import build_reshape_prompt

    if llm is None:
        from ..llm.factory import build_llm_provider

        llm = build_llm_provider()
    try:
        # A translation or a table can be longer than the answer it reshapes.
        text = llm.generate(build_reshape_prompt(request, turn.answer),
                            max_tokens=RagSettings.from_env().deep_max_answer_tokens)
    except Exception:  # noqa: BLE001 - the normal path answers instead
        logger.warning("reshape generation failed; searching instead", exc_info=True)
        return None
    text = (text or "").strip()
    if not text or leaks_canary(text):
        return None
    text = enforce_link_provenance(text, [turn.answer], SecuritySettings.from_env().link_allowlist)
    text, cited = link_citations(text, _blocks(turn.cited))
    if answer_is_unsafe(strip_citations(text), org_id=org_id, stage="reshape"):
        return None
    # A rewrite that kept no marker (a short table) still rests on those documents.
    return text, cited or list(turn.cited)


def _blocks(cited: list[dict]) -> list:
    """Old citation ``n`` as block ``n``, so ``link_citations`` keeps only real
    old numbers, renumbers them and rebuilds ``cited`` in reading order."""
    top = max((c["n"] for c in cited if isinstance(c.get("n"), int)), default=0)
    blocks: list = [None] * top
    for c in cited:
        if isinstance(c.get("n"), int) and c["n"] >= 1:
            blocks[c["n"] - 1] = SimpleNamespace(
                document_id=c.get("document_id"), document_title=c.get("title"),
                source_provider=c.get("provider"), source_uri=c.get("url"),
            )
    return blocks
