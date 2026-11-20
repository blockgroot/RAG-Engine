"""Request guards: a body-size limit and an Origin check on state-changing calls."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.security.request_guards import BodyLimitMiddleware, OriginCheckMiddleware


def _app(**origin) -> TestClient:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        return {"n": len(await request.body())}

    @app.post("/chat/conversations/{cid}/attachments")
    async def upload(cid: str, request: Request):
        return {"n": len(await request.body())}

    if origin:
        app.add_middleware(OriginCheckMiddleware, **origin)
    app.add_middleware(BodyLimitMiddleware, max_bytes=100, upload_max_bytes=1000)
    return TestClient(app)


def test_an_oversized_body_is_refused_before_the_route_reads_it():
    client = _app()
    assert client.post("/echo", content=b"x" * 100).json() == {"n": 100}
    assert client.post("/echo", content=b"x" * 101).status_code == 413
    # Uploads have their own, larger cap.
    assert client.post("/chat/conversations/c1/attachments", content=b"x" * 500).status_code == 200
    assert client.post("/chat/conversations/c1/attachments", content=b"x" * 1001).status_code == 413


def test_a_body_with_no_length_is_counted_as_it_arrives():
    def chunks():
        for _ in range(5):
            yield b"x" * 40
    assert _app().post("/echo", content=chunks()).status_code == 413


@pytest.mark.parametrize("mode, origin, expected", [
    ("enforce", "https://evil.example", 403),
    ("enforce", "https://hand-book.vercel.app", 200),
    ("enforce", None, 200),                       # Slack, webhooks, curl
    ("report", "https://evil.example", 200),      # logged only
    ("off", "https://evil.example", 200),
])
def test_origin_check(mode, origin, expected):
    client = _app(allowed=["https://hand-book.vercel.app/"], mode=mode)
    headers = {"Origin": origin} if origin else {}
    assert client.post("/echo", content=b"{}", headers=headers).status_code == expected


def test_the_request_host_itself_is_same_origin():
    client = _app(allowed=[], mode="enforce")
    assert client.post("/echo", content=b"{}", headers={"Origin": "http://testserver"}).status_code == 200


def test_upload_cap_fits_a_full_batch_of_files(monkeypatch):
    """The browser sends every picked file in one request: two files under the
    per-file cap must not be refused as one oversized body."""
    from app.api.main import create_app
    from app.config.settings import AttachmentSettings
    from app.security.request_guards import BodyLimitMiddleware

    app = create_app()
    guard = next(m for m in app.user_middleware if m.cls is BodyLimitMiddleware)
    a = AttachmentSettings.from_env()
    assert guard.kwargs["upload_max_bytes"] >= a.max_bytes * a.max_per_conversation
