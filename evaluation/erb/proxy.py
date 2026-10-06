"""Token-counting pass-through for Benchmark 1 (docs/benchmarks/benchmark-1-plan.md §6.1).

Every system under test (Handbook, Onyx, baselines) sends its chat calls here,
so all of them are counted the same way, from the provider's own ``usage``
field — never from either app's internal metering. One JSON line per upstream
call goes to ``BENCH_PROXY_LOG``.

Also pins the two things rule 2 says must not differ between systems: the
model (any requested model name is replaced) and the reasoning effort.

A 429 is waited out and retried here, so a rate limit costs time, never a
failed answer in one system and not the other; the wait is logged.

Run:  .venv/bin/python -m evaluation.erb.proxy   (reads .env.bench)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

load_dotenv(".env.bench")

UPSTREAM = os.getenv("BENCH_UPSTREAM_BASE", "https://api.groq.com/openai/v1").rstrip("/")
UPSTREAM_KEY = os.getenv("BENCH_UPSTREAM_KEY") or os.environ["GROQ_API_KEY"]
MODEL = os.getenv("BENCH_MODEL", "openai/gpt-oss-120b")
REASONING = os.getenv("BENCH_REASONING_EFFORT", "medium")
LOG = Path(os.getenv("BENCH_PROXY_LOG", "evaluation/reports/bench1/proxy.jsonl"))
# Full request messages + response text per call: what the reviewer checks
# groundedness against (the exact context the model saw). Large; never committed.
BODIES = LOG.with_name("bodies.jsonl")
MAX_TRIES = 12

app = FastAPI()
# NVIDIA's free tier occasionally accepts a call and never answers. 150 s is
# well past the slowest real call seen (~50 s), so a call that silent is a
# provider hang: retried here and logged as waiting, like a 429, because it
# is the free tier's latency, not the product's.
_client = httpx.AsyncClient(timeout=httpx.Timeout(150.0, connect=10.0))


def _wait_seconds(resp: httpx.Response) -> float:
    """Seconds to wait after a 429, from the provider's own headers."""
    ra = resp.headers.get("retry-after")
    if ra:
        try:
            return float(ra) + 0.5
        except ValueError:
            pass
    # Groq: "x-ratelimit-reset-tokens: 1m26.4s" / "697ms"
    reset = resp.headers.get("x-ratelimit-reset-tokens") or ""
    m = re.fullmatch(r"(?:(\d+)m)?(?:([\d.]+)s)?|([\d.]+)ms", reset)
    if m and any(m.groups()):
        if m.group(3):
            return float(m.group(3)) / 1000 + 0.5
        return int(m.group(1) or 0) * 60 + float(m.group(2) or 0) + 0.5
    return 10.0


def _fingerprint(body: dict) -> dict:
    msgs = body.get("messages") or []
    system = next((m for m in msgs if m.get("role") in ("system", "developer")), None)
    user = next((m for m in msgs if m.get("role") == "user"), None)

    def text(m):
        c = (m or {}).get("content") or ""
        return c if isinstance(c, str) else json.dumps(c)

    return {
        "system_sha": hashlib.sha256(text(system)[:200].encode()).hexdigest()[:12] if system else None,
        "system_head": text(system)[:100] if system else None,
        "first_user_head": text(user)[:100] if user else None,
        "n_messages": len(msgs),
        "n_tools": len(body.get("tools") or []),
        "tool_names": [(t.get("function") or {}).get("name") for t in body.get("tools") or []],
        "tool_choice": body.get("tool_choice"),
        "request_chars": len(json.dumps(msgs)),
    }


def _usage_fields(usage: dict | None) -> dict:
    usage = usage or {}
    details = usage.get("completion_tokens_details") or {}
    return {
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
    }


def _log(row: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(row) + "\n")


def _log_body(call_id: str, body: dict, content: str | None) -> None:
    with BODIES.open("a") as f:
        f.write(json.dumps({"call_id": call_id, "messages": body.get("messages"), "response": content}) + "\n")


async def _send(body: dict, stream: bool) -> tuple[httpx.Response, float]:
    """POST upstream, waiting out 429s. Returns the response and seconds waited."""
    waited, resp = 0.0, None
    headers = {"Authorization": f"Bearer {UPSTREAM_KEY}"}
    for _ in range(MAX_TRIES):
        req = _client.build_request("POST", f"{UPSTREAM}/chat/completions", json=body, headers=headers)
        t0 = time.time()
        try:
            resp = await _client.send(req, stream=stream)
        except httpx.TransportError:  # timeout or dropped connection
            waited += time.time() - t0
            continue
        if resp.status_code != 429:
            return resp, waited
        body_bytes = await resp.aread()
        if b"Request too large" in body_bytes:
            # One request bigger than the per-minute cap: waiting cannot fix it.
            return resp, waited
        pause = _wait_seconds(resp)
        waited += pause
        await _sleep(pause)
    if resp is None:
        raise RuntimeError(f"upstream silent on {MAX_TRIES} tries")
    return resp, waited


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


@app.get("/v1/models")
@app.get("/models")
async def models() -> dict:
    return {"object": "list", "data": [{"id": "bench-answer", "object": "model", "owned_by": "bench"}]}


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat(request: Request):
    body = await request.json()
    requested_model = body.get("model")
    body["model"] = MODEL
    # Pinned the same for every system: a fixed value, or (empty) never sent,
    # so no client can choose its own.
    body.pop("reasoning_effort", None)
    body.pop("reasoning", None)
    if REASONING:
        body["reasoning_effort"] = REASONING
    stream = bool(body.get("stream"))
    if stream:
        body["stream_options"] = {"include_usage": True}
    row = {
        "call_id": uuid.uuid4().hex,
        "ts_start": time.time(),
        "client": request.headers.get("x-bench-client") or request.headers.get("user-agent", "")[:60],
        "requested_model": requested_model,
        "model": MODEL,
        "reasoning_effort": REASONING,
        "stream": stream,
        **_fingerprint(body),
    }

    resp, waited = await _send(body, stream)
    row["waited_s"] = round(waited, 2)
    row["status"] = resp.status_code

    if not stream:
        data = resp.json()
        row.update(_usage_fields(data.get("usage")))
        if resp.status_code != 200:
            row["error"] = str(data)[:300]
        row["ts_end"] = time.time()
        _log(row)
        msg = ((data.get("choices") or [{}])[0].get("message") or {})
        _log_body(row["call_id"], body, msg.get("content") or json.dumps(msg.get("tool_calls")))
        return JSONResponse(data, status_code=resp.status_code)

    async def relay():
        usage, parts = None, []
        try:
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        chunk = json.loads(line[6:])
                        usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage") or usage
                        delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
                        if delta:
                            row.setdefault("ts_first_content", time.time())  # time to first word
                            parts.append(delta)
                    except json.JSONDecodeError:
                        pass
                yield line + "\n"
        finally:
            await resp.aclose()
            row.update(_usage_fields(usage))
            row["ts_end"] = time.time()
            _log(row)
            _log_body(row["call_id"], body, "".join(parts))

    return StreamingResponse(relay(), status_code=resp.status_code, media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("BENCH_PROXY_PORT", "4000")), log_level="warning")
