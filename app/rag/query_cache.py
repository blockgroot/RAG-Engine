"""Postgres-backed query→answer cache (Phase 19).

Keyed by ``(org_id, normalized_question)`` with a short TTL. Standalone questions
only (no ``conversation_id``) — follow-ups depend on memory context.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from ..config.settings import QueryCacheSettings
from ..db.connection import get_connection
from ..vectorstore.base import RetrievedChunk


def normalize_question(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _question_hash(normalized: str, workspace_id: str | None = None) -> str:
    # Workspace-within-a-Workspace: folding workspace_id into the hash input
    # (rather than adding a column to query_answer_cache) keeps an org-wide
    # cache entry and a sub-workspace's cache entry for the identical question
    # text from ever colliding — a workspace's answer must never be served
    # from (or overwrite) the org-wide cache slot, or vice versa.
    key = normalized if workspace_id is None else f"{normalized}|ws:{workspace_id}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _chunk_to_dict(c: RetrievedChunk) -> dict:
    return asdict(c)


def _chunk_from_dict(d: dict) -> RetrievedChunk:
    return RetrievedChunk(**d)


def _serialize(result) -> dict:
    from dataclasses import asdict

    d = asdict(result)
    d["sources"] = [_chunk_to_dict(c) for c in result.sources]
    d.pop("cache_hit", None)
    return d


def _deserialize(payload: dict):
    from .pipeline import RagResult

    sources = [_chunk_from_dict(c) for c in payload.get("sources", [])]
    payload = {k: v for k, v in payload.items() if k != "sources"}
    return RagResult(sources=sources, **payload)


def _valid_org_id(org_id: str) -> bool:
    try:
        uuid.UUID(org_id)
        return True
    except ValueError:
        return False


class QueryAnswerCache:
    def __init__(self, settings: QueryCacheSettings | None = None) -> None:
        self._settings = settings or QueryCacheSettings.from_env()

    def get(self, org_id: str, question: str, workspace_id: str | None = None):
        if not self._settings.enabled or not _valid_org_id(org_id):
            return None

        normalized = normalize_question(question)
        qhash = _question_hash(normalized, workspace_id)
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT payload
                FROM query_answer_cache
                WHERE org_id = %s::uuid
                  AND question_hash = %s
                  AND expires_at > now()
                """,
                (org_id, qhash),
            ).fetchone()
        if not row:
            return None
        result = _deserialize(row[0])
        return replace(result, cache_hit=True)

    def put(self, org_id: str, question: str, result, workspace_id: str | None = None) -> None:
        if not self._settings.enabled or not _valid_org_id(org_id):
            return
        normalized = normalize_question(question)
        qhash = _question_hash(normalized, workspace_id)
        expires = datetime.now(timezone.utc) + timedelta(seconds=self._settings.ttl_seconds)
        payload = _serialize(result)
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO query_answer_cache (
                    org_id, question_hash, normalized_question, payload, expires_at
                )
                VALUES (%s::uuid, %s, %s, %s::jsonb, %s)
                ON CONFLICT (org_id, question_hash) DO UPDATE SET
                    normalized_question = EXCLUDED.normalized_question,
                    payload = EXCLUDED.payload,
                    expires_at = EXCLUDED.expires_at,
                    created_at = now()
                """,
                (org_id, qhash, normalized, json.dumps(payload), expires),
            )


def prune_expired(limit: int = 10_000) -> int:
    """Delete already-expired cache rows. Returns how many went.

    ``get`` filters on ``expires_at > now()``, so an expired row is invisible —
    but nothing ever *removed* it, and the table only grew: one row per distinct
    question per org, forever, on a deployment whose Postgres has a 500MB
    ceiling. (There was even an index on ``expires_at`` with no reader.) Same
    slow-leak shape as the per-org SymSpell dictionaries, which were bounded for
    exactly this reason.

    ``limit`` caps one sweep so a long-neglected table cannot turn a routine
    maintenance tick into a multi-second DELETE holding locks; the next tick
    picks up where this one stopped.
    """
    with get_connection() as conn:
        rows = conn.execute(
            """
            DELETE FROM query_answer_cache
            WHERE ctid IN (
                SELECT ctid FROM query_answer_cache
                WHERE expires_at <= now()
                LIMIT %s
            )
            RETURNING 1
            """,
            (limit,),
        ).fetchall()
    return len(rows)
