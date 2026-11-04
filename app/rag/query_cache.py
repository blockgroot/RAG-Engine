"""Query→answer cache (Phase 19).

Keyed by ``(org_id, normalized_question)`` with a short TTL. Standalone questions
only (no ``conversation_id``) — follow-ups depend on memory context.

Stored in Redis when ``REDIS_URL`` is set, in Postgres otherwise (an install
without Redis keeps its cache). In Redis an entry is
``qa:<org>:<generation>:<hash>`` and expires on its own; clearing an org after
a sync raises its generation, so every older entry is unreachable at once and
left to expire. The org is in every key: an answer carries that company's
passages, and the key is the only thing keeping it with them. A Redis fault is
a miss, never a failed answer.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

from ..config.settings import QueryCacheSettings
from ..db.connection import get_connection
from ..llm.routed import selected_model
from ..vectorstore.base import DateRange, RetrievedChunk

logger = logging.getLogger(__name__)

# An answer bigger than this is not cached in Redis: the instance also holds
# the rate-limit counts, and evicting those resets everyone's allowance.
_MAX_REDIS_PAYLOAD_BYTES = 256_000

_redis_clients: dict[str, object] = {}


def _redis(url: str):
    client = _redis_clients.get(url)
    if client is None:
        import redis

        # Short timeouts, like the rate limiter: a slow Redis is a miss.
        client = redis.Redis.from_url(url, socket_connect_timeout=0.5, socket_timeout=0.5)
        _redis_clients[url] = client
    return client


def _redis_key(org_id: str, generation: bytes | None, qhash: str) -> str:
    return f"qa:{org_id}:{int(generation or 0)}:{qhash}"


def _generation_key(org_id: str) -> str:
    return f"qa:gen:{org_id}"


def normalize_question(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _question_hash(
    normalized: str,
    workspace_id: str | None = None,
    source_provider: str | None = None,
    date_range: DateRange | None = None,
    tags: list[str] | None = None,
) -> str:
    # Workspace-within-a-Workspace: folding workspace_id into the hash input
    # (rather than adding a column to query_answer_cache) keeps an org-wide
    # cache entry and a sub-workspace's cache entry for the identical question
    # text from ever colliding — a workspace's answer must never be served
    # from (or overwrite) the org-wide cache slot, or vice versa.
    #
    # ``source_provider`` is folded in for exactly the same reason: "what did
    # we decide about pricing?" asked of the Slack agent and of the docs agent
    # are different questions over different corpora, so they must not share a
    # slot. Appended only when set, so every pre-existing key is unchanged.
    #
    # ``date_range`` follows the identical reasoning: the same question text
    # filtered to "this quarter" vs. unfiltered is a different question with a
    # different correct answer, so it must never share (or overwrite) another
    # range's cache slot.
    key = normalized if workspace_id is None else f"{normalized}|ws:{workspace_id}"
    if source_provider is not None:
        key = f"{key}|src:{source_provider}"
    if date_range is not None and (date_range.after or date_range.before):
        key = f"{key}|dr:{date_range.after}:{date_range.before}"
    if tags:
        key = f"{key}|tags:{','.join(sorted(tags))}"
    # The selected model follows the identical reasoning, and is the one
    # ingredient read from the request context rather than passed in: the
    # choice is already request-scoped (see app/llm/routed.py), and reading it
    # here means a future call site cannot forget to fold it in. Getting this
    # wrong is not a slow cache, it is a WRONG answer — one member's Gemini
    # answer served verbatim to another member who explicitly asked on a
    # different model. Appended only when set, so every pre-existing key for an
    # un-overridden request is byte-identical to before.
    model = selected_model()
    if model is not None:
        key = f"{key}|model:{model}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _chunk_to_dict(c: RetrievedChunk) -> dict:
    """JSON-safe dict for one hit.

    ``last_modified`` is a ``datetime`` (provenance), and ``json.dumps`` has no
    encoder for it -- a bare ``asdict`` raises. This went unseen because the
    cache is only written when ``conversation_id is None``, and the web chat
    always opens a conversation first; the first caller to pass ``None`` (the
    Slack bot) hit it immediately. ISO strings round-trip exactly and stay
    readable in the stored jsonb.
    """
    d = asdict(c)
    when = d.get("last_modified")
    if isinstance(when, datetime):
        d["last_modified"] = when.isoformat()
    return d


def _chunk_from_dict(d: dict) -> RetrievedChunk:
    when = d.get("last_modified")
    if isinstance(when, str):
        try:
            d = {**d, "last_modified": datetime.fromisoformat(when)}
        except ValueError:
            # A stored value we cannot parse must not poison every later read
            # of this org's cache -- provenance is optional everywhere it is
            # consumed, so dropping it degrades the header, not the answer.
            d = {**d, "last_modified": None}
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

    def get(
        self,
        org_id: str,
        question: str,
        workspace_id: str | None = None,
        source_provider: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
    ):
        if not self._settings.enabled or not _valid_org_id(org_id):
            return None

        normalized = normalize_question(question)
        qhash = _question_hash(normalized, workspace_id, source_provider, date_range, tags)
        if self._settings.redis_url:
            payload = self._redis_get(org_id, qhash)
        else:
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
            payload = row[0] if row else None
        if not payload:
            return None
        result = _deserialize(payload)
        return replace(result, cache_hit=True)

    def _redis_get(self, org_id: str, qhash: str) -> dict | None:
        try:
            client = _redis(self._settings.redis_url)
            raw = client.get(_redis_key(org_id, client.get(_generation_key(org_id)), qhash))
        except Exception as exc:  # noqa: BLE001 - a cache fault is a miss
            logger.warning("Answer cache (Redis) unavailable, treated as a miss: %s", exc)
            return None
        return json.loads(raw) if raw else None

    def put(
        self,
        org_id: str,
        question: str,
        result,
        workspace_id: str | None = None,
        source_provider: str | None = None,
        date_range: DateRange | None = None,
        tags: list[str] | None = None,
    ) -> None:
        if not self._settings.enabled or not _valid_org_id(org_id):
            return
        normalized = normalize_question(question)
        qhash = _question_hash(normalized, workspace_id, source_provider, date_range, tags)
        payload = _serialize(result)
        if self._settings.redis_url:
            self._redis_put(org_id, qhash, json.dumps(payload))
            return
        expires = datetime.now(timezone.utc) + timedelta(seconds=self._settings.ttl_seconds)
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

    def _redis_put(self, org_id: str, qhash: str, body: str) -> None:
        if len(body) > _MAX_REDIS_PAYLOAD_BYTES:
            return
        try:
            client = _redis(self._settings.redis_url)
            # Read the generation first: a sync landing in between stores this
            # under the old one, where it is never read. The safe direction.
            key = _redis_key(org_id, client.get(_generation_key(org_id)), qhash)
            client.set(key, body, ex=self._settings.ttl_seconds)
        except Exception as exc:  # noqa: BLE001 - not caching is fine
            logger.warning("Answer cache (Redis) unavailable, answer not cached: %s", exc)


def delete_org_entries(org_id: str) -> int:
    """Drop every cached answer for one org. Returns how many rows went.

    Called after a successful ingest. Without this, a sync that adds new
    content is invisible for up to the cache TTL: the question was already
    asked, so the pre-sync answer is served straight back, and to the person
    who just pressed Update it reads as "the sync did nothing". Correctness
    beats a cache hit here — the cache exists to save a repeat LLM call, and
    the whole point of a repeat call after new content is that the answer may
    differ.

    Org-wide rather than per-provider on purpose: the provider is folded into
    the question HASH, not stored as a column, so there is nothing to filter
    on — and a cross-source question ("what did we decide about pricing")
    could be answered from the corpus that just changed anyway.

    In Redis this raises the org's generation (one command); the count is 0,
    since the older entries are left to expire rather than found and deleted.
    If Redis cannot be reached, the old answers stay until they expire (the
    TTL), and that is logged as an error.
    """
    settings = QueryCacheSettings.from_env()
    if settings.redis_url:
        try:
            _redis(settings.redis_url).incr(_generation_key(org_id))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Answer cache (Redis) not cleared for org %s; old answers last up to %ss: %s",
                org_id, settings.ttl_seconds, exc,
            )
        return 0
    with get_connection() as conn:
        rows = conn.execute(
            "DELETE FROM query_answer_cache WHERE org_id = %s RETURNING 1",
            (org_id,),
        ).fetchall()
    return len(rows)


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
