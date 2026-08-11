"""Pin the same-origin /api rewrite that makes SameSite=Lax work on split hosts."""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def test_next_config_rewrites_api_to_the_backend():
    text = (_REPO / "frontend" / "next.config.js").read_text()
    assert 'source: "/api/:path*"' in text
    assert "API_PROXY_TARGET" in text
    assert "destination:" in text


def test_frontend_defaults_to_same_origin_api_base():
    text = (_REPO / "frontend" / "lib" / "api.ts").read_text()
    assert 'process.env.NEXT_PUBLIC_API_BASE_URL || "/api"' in text
