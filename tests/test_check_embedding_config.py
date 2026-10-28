"""The boot check refuses a model whose vectors do not fit the database.

A wrong size would otherwise boot and then fail every sync with a database
error that does not name the cause. A provider that is merely down must not
fail a deploy.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from app.core.exceptions import EmbeddingProviderError
from app.embeddings.remote import RemoteEmbeddingProvider

_path = Path(__file__).resolve().parent.parent / "scripts" / "check_embedding_config.py"
_spec = importlib.util.spec_from_file_location("check_embedding_config", _path)
check_embedding_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_embedding_config)


class _Provider:
    def __init__(self, size=None, error=None):
        self.size, self.error = size, error

    def embed(self, texts):
        if self.error:
            raise self.error
        return [[0.0] * self.size for _ in texts]


def test_a_matching_size_passes():
    assert check_embedding_config.check(_Provider(size=1024), 1024) is None


def test_a_different_size_is_refused_and_named():
    problem = check_embedding_config.check(_Provider(size=768), 1024)
    assert problem is not None
    assert "768" in problem and "1024" in problem
    assert "EMBEDDING_SEND_DIMENSIONS" in problem


def test_an_unreachable_service_does_not_block_the_boot():
    down = _Provider(error=EmbeddingProviderError("connection refused"))
    assert check_embedding_config.check(down, 1024) is None


def _captured_request(dimensions):
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.0])])

    provider = RemoteEmbeddingProvider(
        model="m", api_key="k", base_url="https://example.com/v1", dimensions=dimensions
    )
    provider._client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    provider.embed(["hello"])
    return seen


def test_dimensions_are_sent_only_when_asked_for():
    # Unset: the request is exactly as before, so a provider without the
    # option (and today's Cloud on Jina) sees no change.
    assert "dimensions" not in _captured_request(None)
    assert _captured_request(1024)["dimensions"] == 1024
