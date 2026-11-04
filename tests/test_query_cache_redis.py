"""The answer cache in Redis: per company, cleared by a sync, a fault is a miss."""

from __future__ import annotations

import socket
import uuid

import pytest

from app.config.settings import QueryCacheSettings
from app.rag import query_cache
from app.rag.pipeline import RagResult
from app.rag.query_cache import QueryAnswerCache, delete_org_entries
from app.vectorstore.base import RetrievedChunk


def _answer(text: str = "Twenty days of leave.") -> RagResult:
    hit = RetrievedChunk(
        content="Leave: 20 days", score=0.8, document_id="d1", chunk_index=0, org_id="o1",
        document_title="Leave policy",
    )
    return RagResult(answer=text, answered=True, source="policy", sources=[hit])


@pytest.fixture
def cache(redis_url, monkeypatch):
    monkeypatch.setenv("REDIS_URL", redis_url)  # delete_org_entries reads the env
    return QueryAnswerCache(QueryCacheSettings(enabled=True, ttl_seconds=60, redis_url=redis_url))


def _org() -> str:
    return str(uuid.uuid4())


def test_an_answer_comes_back_from_redis_and_expires(cache, redis_url):
    import redis

    org = _org()
    cache.put(org, "How much leave?", _answer())
    hit = cache.get(org, "  how much   LEAVE? ")  # normalized
    assert hit.cache_hit and hit.answer == "Twenty days of leave."
    assert hit.sources[0].document_title == "Leave policy"
    [key] = redis.Redis.from_url(redis_url).keys(f"qa:{org}:*")
    assert 0 < redis.Redis.from_url(redis_url).ttl(key) <= 60


def test_one_company_never_reads_another_companys_answer(cache):
    org, other = _org(), _org()
    cache.put(org, "How much leave?", _answer())
    assert cache.get(other, "How much leave?") is None


def test_a_sync_clears_that_company_only(cache):
    org, other = _org(), _org()
    cache.put(org, "How much leave?", _answer())
    cache.put(other, "How much leave?", _answer("Other company's answer."))

    delete_org_entries(org)

    assert cache.get(org, "How much leave?") is None
    assert cache.get(other, "How much leave?").answer == "Other company's answer."
    cache.put(org, "How much leave?", _answer("After the sync."))
    assert cache.get(org, "How much leave?").answer == "After the sync."


def test_a_huge_answer_is_not_cached(cache):
    org = _org()
    cache.put(org, "Everything?", _answer("x" * (query_cache._MAX_REDIS_PAYLOAD_BYTES + 1)))
    assert cache.get(org, "Everything?") is None


def test_unreachable_redis_is_a_miss_never_an_error(monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"redis://127.0.0.1:{port}/0"
    monkeypatch.setenv("REDIS_URL", url)
    cache = QueryAnswerCache(QueryCacheSettings(enabled=True, ttl_seconds=60, redis_url=url))
    org = _org()
    cache.put(org, "How much leave?", _answer())
    assert cache.get(org, "How much leave?") is None
    assert delete_org_entries(org) == 0
