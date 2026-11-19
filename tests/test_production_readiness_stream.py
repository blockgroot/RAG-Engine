"""A client that leaves mid-answer: the budget empties (optional stages skip)
and the answer places stay held until the running step really ends."""

from __future__ import annotations

import asyncio
import threading

from app.api import chat
from app.rag.request_budget import RequestBudget


def test_leaving_empties_the_budget_and_holds_places_until_the_step_ends(monkeypatch):
    freed: list[str] = []
    monkeypatch.setattr(chat, "take_place", lambda key, *a, **k: f"tok:{key}")
    monkeypatch.setattr(chat, "give_back_place", lambda key, tok, s: tok and freed.append(key))
    release = threading.Event()
    seen_budget: list[bool] = []

    def stream():
        yield "first"
        release.wait(5)  # a long step still running when the client leaves
        seen_budget.append(RequestBudget(deadline=10**12).can_spend(1))
        yield "second"

    settings = type("S", (), {"chat_max_concurrent_per_person": 1, "chat_max_concurrent": 1,
                              "chat_queue_wait_seconds": 1})()

    async def run():
        gen = chat._in_a_slot(stream(), settings, "u1")
        assert await gen.__anext__() == "first"
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.05)
        task.cancel()  # the client disconnected
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert freed == [], "places must stay held while the step still runs"
        release.set()
        for _ in range(100):
            if len(freed) == 2:
                break
            await asyncio.sleep(0.02)

    asyncio.run(run())
    assert sorted(freed) == ["answers:all", "answers:user:u1"]
    assert seen_budget == [False], "optional stages after a disconnect must be skipped"


def test_a_finished_stream_frees_its_places_at_once(monkeypatch):
    freed: list[str] = []
    monkeypatch.setattr(chat, "take_place", lambda key, *a, **k: f"tok:{key}")
    monkeypatch.setattr(chat, "give_back_place", lambda key, tok, s: tok and freed.append(key))
    settings = type("S", (), {"chat_max_concurrent_per_person": 1, "chat_max_concurrent": 1,
                              "chat_queue_wait_seconds": 1})()

    async def run():
        return [e async for e in chat._in_a_slot(iter(["a", "b"]), settings, "u1")]

    assert asyncio.run(run()) == ["a", "b"]
    assert sorted(freed) == ["answers:all", "answers:user:u1"]
