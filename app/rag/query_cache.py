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


def _question_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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

    def get(self, org_id: str, question: str):
        if not self._settings.enabled or not _valid_org_id(org_id):
            return None

        normalized = normalize_question(question)
        qhash = _question_hash(normalized)
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

    def put(self, org_id: str, question: str, result) -> None:
        if not self._settings.enabled or not _valid_org_id(org_id):
            return
        normalized = normalize_question(question)
        qhash = _question_hash(normalized)
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
