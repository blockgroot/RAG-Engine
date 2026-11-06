"""The chat answer caps: a person's own few, and everyone's shared cap.

At most N answers at once, a waiter is told, a timeout is "busy"; a person
over their own cap is told at once. Fresh places per test (conftest).
"""

from __future__ import annotations

import asyncio

from app.api import chat
from app.config.settings import RateLimitSettings

TOKEN = "event: token\ndata: \"hi\"\n\n"


def _answer():
    yield TOKEN


def _settings(n: int, wait: float = 0.05, per_person: int = 0) -> RateLimitSettings:
    return RateLimitSettings(
        chat_max_concurrent=n,
        chat_queue_wait_seconds=wait,
        chat_max_concurrent_per_person=per_person,
    )


def _slot(settings: RateLimitSettings, user: str = "u1"):
    return chat._in_a_slot(_answer(), settings, user)


async def _collect(stream) -> list[str]:
    return [event async for event in stream]


def test_free_slot_streams_unchanged():
    assert asyncio.run(_collect(_slot(_settings(1)))) == [TOKEN]


def test_full_slots_wait_then_busy():
    async def scenario():
        holder = _slot(_settings(1), "u1")
        await holder.__anext__()  # holds the only slot
        return await _collect(_slot(_settings(1), "u2"))

    events = asyncio.run(scenario())
    assert events[0].startswith("event: status")
    assert events[1].startswith("event: error") and "try again" in events[1]
    assert len(events) == 2


def test_waiter_gets_the_slot_when_it_frees():
    async def scenario():
        holder = _slot(_settings(1), "u1")
        await holder.__anext__()
        asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(holder.aclose()))
        return await _collect(_slot(_settings(1, wait=2), "u2"))

    events = asyncio.run(scenario())
    assert events[0].startswith("event: status")
    assert events[1:] == [TOKEN]


def test_slot_released_when_answer_finishes():
    async def scenario():
        await _collect(_slot(_settings(1)))
        return await _collect(_slot(_settings(1)))

    assert asyncio.run(scenario()) == [TOKEN]


def test_a_person_over_their_own_cap_is_told_at_once():
    settings = _settings(20, per_person=2)

    async def scenario():
        first, second = _slot(settings, "u1"), _slot(settings, "u1")
        await first.__anext__()
        await second.__anext__()
        third = await _collect(_slot(settings, "u1"))
        someone_else = await _collect(_slot(settings, "u2"))
        return third, someone_else

    third, someone_else = asyncio.run(scenario())
    assert len(third) == 1 and third[0].startswith("event: error")
    assert "You already have 2 answers being written" in third[0]
    assert someone_else == [TOKEN]


def test_a_waiter_is_logged_once_not_on_every_retry(caplog):
    async def scenario():
        holder = _slot(_settings(1), "u1")
        await holder.__anext__()
        return await _collect(_slot(_settings(1, wait=1), "u2"))  # ~4 retries

    with caplog.at_level("WARNING", logger="app.security.rate_limit"):
        events = asyncio.run(scenario())
    assert events[-1].startswith("event: error")
    refused = [r for r in caplog.records if "rate limit refused" in r.getMessage()]
    assert len(refused) == 1


def test_leaving_mid_answer_gives_both_places_back():
    settings = _settings(1, per_person=1)

    async def scenario():
        holder = _slot(settings, "u1")
        await holder.__anext__()
        await holder.aclose()  # the client left
        return await _collect(_slot(settings, "u1"))

    assert asyncio.run(scenario()) == [TOKEN]
