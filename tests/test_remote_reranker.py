"""Unit tests for the remote (Jina) reranker — no network."""

from __future__ import annotations

import httpx
import pytest

from app.core.exceptions import ConfigurationError, ProviderError
from app.reranker import build_reranker, clear_reranker_cache
from app.reranker.remote import RemoteReranker
from app.vectorstore.base import RetrievedChunk


def _chunk(i: int, text: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        content=text,
        score=score,
        document_id=f"doc-{i}",
        chunk_index=i,
        org_id="org-1",
    )


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.jina.ai/v1/rerank")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> dict:
        return self._payload


def test_remote_reranker_reorders_and_preserves_cosine(monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [
        _chunk(0, "cafeteria menu", 0.55),
        _chunk(1, "annual leave is 20 days", 0.40),
        _chunk(2, "sick leave is 10 days", 0.42),
    ]
    captured: dict = {}

    def fake_post(self, path: str, json: dict | None = None):  # noqa: A002
        captured["path"] = path
        captured["json"] = json
        return _FakeResponse(
            {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 2, "relevance_score": 0.8},
                ]
            }
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    reranker = RemoteReranker(
        model="jina-reranker-v3",
        api_key="test-key",
        base_url="https://api.jina.ai/v1",
    )
    out = reranker.rerank("how many leave days", candidates, top_k=2)

    assert [c.content for c in out] == [
        "annual leave is 20 days",
        "sick leave is 10 days",
    ]
    # Cosine scores must survive for the confidence gate.
    assert out[0].score == 0.40
    assert out[1].score == 0.42
    assert captured["path"] == "/rerank"
    assert captured["json"]["model"] == "jina-reranker-v3"
    assert captured["json"]["top_n"] == 2
    assert len(captured["json"]["documents"]) == 3


def test_remote_reranker_requires_api_key() -> None:
    with pytest.raises(ConfigurationError):
        RemoteReranker(model="jina-reranker-v3", api_key="", base_url="https://api.jina.ai/v1")


def test_remote_reranker_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(self, path: str, json: dict | None = None):  # noqa: A002
        return _FakeResponse({}, status_code=429)

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    reranker = RemoteReranker(
        model="jina-reranker-v3",
        api_key="test-key",
        base_url="https://api.jina.ai/v1",
    )
    with pytest.raises(ProviderError):
        reranker.rerank("q", [_chunk(0, "a", 0.5)], top_k=1)


def test_factory_remote_uses_embedding_key_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_reranker_cache()
    monkeypatch.setenv("RERANKER_BACKEND", "remote")
    monkeypatch.setenv("RERANKER_MODEL", "jina-reranker-v3")
    monkeypatch.delenv("RERANKER_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDING_API_KEY", "shared-jina-key")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://api.jina.ai/v1")

    reranker = build_reranker()
    assert isinstance(reranker, RemoteReranker)
    assert reranker.api_key == "shared-jina-key"
    assert reranker.model == "jina-reranker-v3"
