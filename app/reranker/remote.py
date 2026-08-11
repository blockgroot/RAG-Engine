"""Remote reranker via a Jina-compatible ``/v1/rerank`` HTTP endpoint.

Used when the deploy host cannot load a multi-GB local cross-encoder
(``bge-reranker-v2-m3``). Same ``Reranker`` contract as the local backend:
reorders candidates and **preserves each chunk's cosine ``.score``** so the
Phase 3 confidence gate stays calibrated.

Default target is Jina (``https://api.jina.ai/v1``) — OpenAI-compatible
embeddings already sit on the same host when ``EMBEDDING_BACKEND=remote``.
"""

from __future__ import annotations

import httpx

from ..core.exceptions import ConfigurationError, ProviderError
from ..vectorstore.base import RetrievedChunk
from .base import Reranker

DEFAULT_REMOTE_MODEL = "jina-reranker-v3"
DEFAULT_REMOTE_BASE_URL = "https://api.jina.ai/v1"
DEFAULT_TIMEOUT = 30.0


class RemoteReranker(Reranker):
    """Rerank via ``POST {base_url}/rerank`` (Jina wire format)."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = DEFAULT_REMOTE_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not model:
            raise ConfigurationError("RERANKER_MODEL is required for the remote backend")
        if not api_key:
            raise ConfigurationError(
                "RERANKER_API_KEY (or EMBEDDING_API_KEY) is required for the remote reranker"
            )
        if not base_url:
            raise ConfigurationError("RERANKER_BASE_URL is required for the remote backend")

        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )

    def rerank(
        self, query: str, candidates: list[RetrievedChunk], top_k: int
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        if top_k <= 0:
            return []

        payload = {
            "model": self.model,
            "query": query,
            "documents": [c.content for c in candidates],
            "top_n": min(top_k, len(candidates)),
        }
        try:
            response = self._client.post("/rerank", json=payload)
            response.raise_for_status()
            body = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderError(
                f"Rerank request timed out after {self.timeout}s", cause=exc
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Rerank API error from {self.base_url}: {exc}", cause=exc
            ) from exc
        except ValueError as exc:
            raise ProviderError("Rerank API returned non-JSON body", cause=exc) from exc

        results = body.get("results")
        if not isinstance(results, list):
            raise ProviderError("Rerank API response missing a results list")

        ordered: list[RetrievedChunk] = []
        seen: set[int] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(candidates):
                continue
            if idx in seen:
                continue
            seen.add(idx)
            ordered.append(candidates[idx])
            if len(ordered) >= top_k:
                break

        # If the API returned fewer/invalid rows, pad with remaining candidates
        # in original order so callers still get up to top_k chunks.
        if len(ordered) < top_k:
            for i, chunk in enumerate(candidates):
                if i in seen:
                    continue
                ordered.append(chunk)
                if len(ordered) >= top_k:
                    break
        return ordered
