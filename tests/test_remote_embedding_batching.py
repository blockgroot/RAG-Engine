"""RemoteEmbeddingProvider must batch requests (EMBED_BATCH_SIZE), not send an
unbounded number of texts in one call.

Regression for a live incident: a Notion page that chunked into an unusually
large number of pieces sent them all in a single ``embeddings.create`` call
with EMBEDDING_BACKEND=remote, since only the local backend batched. That
plus a memory-constrained deploy (Render free, 512MB) OOM-crashed the
instance before a single document was stored. No live network here — the
OpenAI client is monkeypatched.
"""

from __future__ import annotations

from app.embeddings.remote import RemoteEmbeddingProvider


class _FakeEmbeddingItem:
    def __init__(self, index: int, embedding: list[float]) -> None:
        self.index = index
        self.embedding = embedding


class _FakeResponse:
    def __init__(self, data: list[_FakeEmbeddingItem]) -> None:
        self.data = data


class _FakeEmbeddingsResource:
    def __init__(self, calls: list[list[str]]) -> None:
        self._calls = calls

    def create(self, model: str, input: list[str]):  # noqa: A002 - matches SDK's kwarg name
        self._calls.append(list(input))
        # Return items out of order to exercise the sort-by-index path too.
        items = [
            _FakeEmbeddingItem(i, [float(i)] * 3) for i in range(len(input))
        ]
        return _FakeResponse(list(reversed(items)))


class _FakeClient:
    def __init__(self, calls: list[list[str]]) -> None:
        self.embeddings = _FakeEmbeddingsResource(calls)


def _provider(batch_size: int) -> tuple[RemoteEmbeddingProvider, list[list[str]]]:
    provider = RemoteEmbeddingProvider(
        model="test-model",
        api_key="key",
        base_url="https://example.com/v1",
        batch_size=batch_size,
    )
    calls: list[list[str]] = []
    provider._client = _FakeClient(calls)  # bypass real HTTP client
    return provider, calls


def test_embed_splits_into_batches():
    provider, calls = _provider(batch_size=2)
    texts = [f"chunk-{i}" for i in range(5)]

    vectors = provider.embed(texts)

    assert len(vectors) == 5
    # 5 texts at batch_size=2 -> batches of [2, 2, 1]
    assert [len(c) for c in calls] == [2, 2, 1]
    assert calls[0] == texts[0:2]
    assert calls[1] == texts[2:4]
    assert calls[2] == texts[4:5]


def test_embed_preserves_order_within_and_across_batches():
    provider, _ = _provider(batch_size=2)
    texts = [f"chunk-{i}" for i in range(5)]

    vectors = provider.embed(texts)

    # Each batch's fake response is index-sorted back into order, and batches
    # are concatenated in call order, so vector i must be [float(i % batch), ...]
    # matching the per-batch index used by the fake resource.
    assert vectors[0] == [0.0, 0.0, 0.0]
    assert vectors[1] == [1.0, 1.0, 1.0]


def test_embed_single_call_when_under_batch_size():
    provider, calls = _provider(batch_size=16)
    texts = [f"chunk-{i}" for i in range(5)]

    provider.embed(texts)

    assert len(calls) == 1
    assert calls[0] == texts


def test_embed_empty_list_makes_no_calls():
    provider, calls = _provider(batch_size=16)

    assert provider.embed([]) == []
    assert calls == []
