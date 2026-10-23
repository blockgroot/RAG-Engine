"""Handbook's own MCP servers over Streamable HTTP (JSON replies, no sessions).

``POST /mcp/<name>`` answers ``initialize``, ``tools/list`` and ``tools/call``.
Listing needs no sign-in; a call needs the person's own vendor token as the
bearer, which is only forwarded to the vendor (``app/mcphost``). A sign-in the
vendor no longer accepts is a 401, so Agent mode asks the person to sign in again.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from ..mcphost import VendorAuthError, VendorError, hosted_tools

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mcp-host"])

PROTOCOL_VERSION = "2025-06-18"


def _reply(rid, result=None, error: tuple[int, str] | None = None) -> JSONResponse:
    body = {"jsonrpc": "2.0", "id": rid}
    body.update({"error": {"code": error[0], "message": error[1]}} if error else {"result": result})
    return JSONResponse(body)


@router.post("/mcp/{name}")
async def serve(name: str, request: Request):
    tools = hosted_tools(name)
    if tools is None:
        return Response(status_code=404)
    try:
        message = await request.json()
    except ValueError:
        return _reply(None, error=(-32700, "Parse error"))
    if not isinstance(message, dict):
        return _reply(None, error=(-32600, "Invalid request"))
    method, rid = message.get("method"), message.get("id")
    if rid is None:
        return Response(status_code=202)        # a notification
    if method == "initialize":
        return _reply(rid, {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": {"name": f"handbook-{name}", "version": "1"}})
    if method == "tools/list":
        return _reply(rid, {"tools": [t.listing() for t in tools]})
    if method != "tools/call":
        return _reply(rid, error=(-32601, "Method not found"))

    params = message.get("params") or {}
    tool = next((t for t in tools if t.name == params.get("name")), None)
    if tool is None:
        return _reply(rid, error=(-32602, "Unknown tool"))
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
    args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    try:
        result = await run_in_threadpool(tool.run, token, args)
    except VendorAuthError:
        return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
    except (VendorError, KeyError, TypeError, ValueError) as exc:
        said = str(exc) if isinstance(exc, VendorError) else f"Missing or invalid input: {exc}"
        return _reply(rid, {"content": [{"type": "text", "text": said}], "isError": True})
    structured = {k: v for k, v in (("id", result.id), ("url", result.url), ("name", result.name)) if v}
    return _reply(rid, {"content": [{"type": "text", "text": result.text}],
                        **({"structuredContent": structured} if structured else {})})
