"""Llama Prompt Guard 2 on Groq's free tier.

The model reads at most ~512 tokens (measured: ~1,800 chars of English is
accepted, 2,000 is a 400), so a longer text is split into overlapping windows
scored in parallel and the text's score is the MAXIMUM — the model card's own
advice, since an injection is usually one sentence inside a normal page. One
failed window makes the whole text ``None``: a max over the windows that
happened to succeed would look complete and could miss the one that mattered.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import httpx

from ..security.untrusted import normalize_untrusted
from .base import InjectionGuard

WINDOW_CHARS = 1500
# An attack sentence straddling a cut is still whole in one window.
OVERLAP_CHARS = 200
MAX_PARALLEL = 4


def windows(text: str) -> list[str]:
    if not text.strip():
        return []
    step = WINDOW_CHARS - OVERLAP_CHARS
    return [text[i : i + WINDOW_CHARS] for i in range(0, max(len(text) - OVERLAP_CHARS, 1), step)]


class GroqPromptGuard(InjectionGuard):
    def __init__(self, *, api_key: str, base_url: str, model: str, timeout: float) -> None:
        self.model = model
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._timeout = timeout

    def _one(self, window: str) -> float | None:
        try:
            response = httpx.post(
                self._url,
                json={"model": self.model, "messages": [{"role": "user", "content": window}]},
                headers=self._headers,
                timeout=self._timeout,
            )
            if response.status_code == 400 and len(window) > 200:
                # Over the model's 512 tokens: dense scripts (CJK) pack far more
                # tokens per char than the English the window was sized on.
                half = len(window) // 2
                parts = [self._one(window[: half + OVERLAP_CHARS // 2]), self._one(window[half - OVERLAP_CHARS // 2 :])]
                return None if None in parts else max(parts)
            response.raise_for_status()
            value = float(response.json()["choices"][0]["message"]["content"])
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            return None
        return value if 0.0 <= value <= 1.0 else None

    def score(self, texts: list[str]) -> list[float | None]:
        # Score what the model would see: hidden characters dropped, lookalikes folded.
        per_text = [windows(normalize_untrusted(t or "")) for t in texts]
        flat = [w for ws in per_text for w in ws]
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
            results = iter(list(pool.map(self._one, flat)))
        out: list[float | None] = []
        for ws in per_text:
            got = [next(results) for _ in ws]
            # Blank text is not an injection, and costs no call.
            out.append(None if any(g is None for g in got) else max(got, default=0.0))
        return out
