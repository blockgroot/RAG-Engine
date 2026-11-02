"""The chat answer cap: at most N answers at once, a waiter is told, a timeout is "busy"."""

from __future__ import annotations

import asyncio

import pytest

from app.api import chat
from app.config.settings import RateLimitSettings

TOKEN = "event: token\ndata: \"hi\"\n\n"


@pytest.fixture(autouse=True)
def _fresh_slots(monkeypatch):
    monkeypatch.setattr(chat, "_answer_slots", None)


def _answer():
    yield TOKEN


def _settings(n: int, wait: float = 0.05) -> RateLimitSettings:
    return RateLimitSettings(chat_max_concurrent=n, chat_queue_wait_seconds=wait)


async def _collect(stream) -> list[str]:
    return [event async for event in stream]


def test_free_slot_streams_unchanged():
    assert asyncio.run(_collect(chat._in_a_slot(_answer(), _settings(1)))) == [TOKEN]


def test_full_slots_wait_then_busy():
    async def scenario():
        holder = chat._in_a_slot(_answer(), _settings(1))
        await holder.__anext__()  # holds the only slot
        return await _collect(chat._in_a_slot(_answer(), _settings(1)))

    events = asyncio.run(scenario())
    assert events[0].startswith("event: status")
    assert events[1].startswith("event: error") and "try again" in events[1]
    assert len(events) == 2


def test_waiter_gets_the_slot_when_it_frees():
    async def scenario():
        holder = chat._in_a_slot(_answer(), _settings(1))
        await holder.__anext__()
        asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(holder.aclose()))
        return await _collect(chat._in_a_slot(_answer(), _settings(1, wait=2)))

    events = asyncio.run(scenario())
    assert events[0].startswith("event: status")
    assert events[1:] == [TOKEN]


def test_slot_released_when_answer_finishes():
    async def scenario():
        await _collect(chat._in_a_slot(_answer(), _settings(1)))
        return await _collect(chat._in_a_slot(_answer(), _settings(1)))

    assert asyncio.run(scenario()) == [TOKEN]
