"""From a question to where the graph should start (Second Brain step 1.5).

Two kinds of match, strongest first, top ``MAX_SEEDS``:

1. **Exact identifiers** in the question: a Linear id (``ENG-142``), a pull
   request (``#14``, ``api#14``) or a repository name -- matched against entity
   aliases and PR names. An identifier is what the asker typed on purpose.
2. **Trigram names**: ``word_similarity`` of an entity's name within the
   question (``pg_trgm``), so "the token refresh bug" finds an issue titled
   "Token refresh fails on Safari". Short names are excluded from fuzzy
   matching -- a three-letter channel called "eng" would match every question
   containing "engineering".

Only entities the viewer may see are candidates, by the same rules the walk
applies (``walk.visible_edge_sql`` / ``openable_entity_sql``): a document they
cannot open is never a seed, and anything else must have at least one current
edge with visible evidence -- otherwise matching its NAME would disclose it.
No LLM call; one round trip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..db.connection import get_connection
from .walk import _acl, openable_entity_sql, visible_edge_sql

MAX_SEEDS = 3
#: ``pg_trgm``'s own default word-similarity threshold.
MIN_WORD_SIMILARITY = 0.6
#: Names shorter than this only ever match exactly.
MIN_FUZZY_NAME = 5

_LINEAR_ID = re.compile(r"\b([A-Za-z][A-Za-z0-9]{1,9}-\d{1,6})\b")
_PR_REF = re.compile(r"(?:\b([\w.-]+))?#(\d{1,7})\b")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{1,60}")


@dataclass(frozen=True)
class Seed:
    id: str
    kind: str
    name: str
    exact: bool
    score: float


def question_identifiers(question: str) -> tuple[list[str], list[str], list[str]]:
    """``(aliases, pr_suffixes, words)`` pulled from the question text."""
    aliases = {m.upper() for m in _LINEAR_ID.findall(question)}
    prs = []
    for repo, number in _PR_REF.findall(question):
        prs.append(f"{repo.lower()}#{number}" if repo else f"#{number}")
    words = {w.lower() for w in _WORD.findall(question)}
    return sorted(aliases), prs, sorted(words)


def link_question(
    org_id: str, workspace_id: str | None, question: str, viewer=None, *, limit: int = MAX_SEEDS
) -> list[Seed]:
    """The best ``limit`` visible entities this question refers to."""
    question = (question or "").strip()
    if not question:
        return []
    aliases, prs, words = question_identifiers(question)
    acl = _acl(viewer)
    edge_ok, edge_params = visible_edge_sql("e", acl)
    node_ok, node_params = openable_entity_sql("x", acl)

    sql = f"""
        SELECT id::text, kind, name, exact, score FROM (
            SELECT x.id, x.kind, x.name,
                   (x.aliases && %s::text[]
                    OR (x.kind = 'pr' AND EXISTS (
                        SELECT 1 FROM unnest(%s::text[]) p
                         WHERE lower(x.name) LIKE '%%' || p))
                    OR (x.kind = 'repo' AND x.aliases && %s::text[])) AS exact,
                   CASE WHEN length(x.name) >= %s
                        THEN word_similarity(lower(x.name), lower(%s)) ELSE 0 END AS score,
                   x.document_id
              FROM kg_entities x
             WHERE x.org_id = %s::uuid
               AND x.workspace_id IS NOT DISTINCT FROM %s::uuid
        ) x
         WHERE (exact OR score >= %s)
           AND {node_ok}
           AND EXISTS (SELECT 1 FROM kg_edges e
                        WHERE (e.src_id = x.id OR e.dst_id = x.id)
                          AND e.valid_to IS NULL AND {edge_ok})
         ORDER BY exact DESC, score DESC, name
         LIMIT %s
    """
    params = [
        aliases, prs, words,
        MIN_FUZZY_NAME, question,
        org_id, workspace_id,
        MIN_WORD_SIMILARITY,
        *node_params, *edge_params,
        limit,
    ]
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [Seed(r[0], r[1], r[2], bool(r[3]), float(r[4] or 0)) for r in rows]
