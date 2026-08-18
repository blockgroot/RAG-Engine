"""First-sync listing retry: it must outlast a slow permission grant.

A real Slack first sync listed zero threads twice, 5s apart, ~60s after the
OAuth grant — then was marked ``succeeded`` with nothing stored, so the admin
had to click "Update" again by hand. The retry schedule now escalates, and
stops the moment the source answers properly.
"""

from __future__ import annotations

from app.ingestion import pipeline
from app.sources.base import SourceRef


class _LaggyAdapter:
    """Returns nothing until the Nth call, like a source index catching up."""

    def __init__(self, ready_on_call: int) -> None:
        self.calls = 0
        self._ready_on_call = ready_on_call

    def list_documents(self) -> list[SourceRef]:
        self.calls += 1
        if self.calls < self._ready_on_call:
            return []
        return [SourceRef(external_id=f"c:{i}", title=f"t{i}") for i in range(4)]


def _no_sleep(monkeypatch) -> list[int]:
    slept: list[int] = []
    monkeypatch.setattr(pipeline.time, "sleep", slept.append)
    return slept


def test_a_slow_grant_is_waited_out_instead_of_needing_a_manual_re_click(monkeypatch):
    slept = _no_sleep(monkeypatch)
    adapter = _LaggyAdapter(ready_on_call=4)  # empty, empty, empty, then real

    refs = pipeline._list_documents_with_first_sync_retry(
        adapter,
        is_first_sync=True,
        retry_delays=pipeline._FIRST_SYNC_INGEST_RETRY_DELAYS,
    )

    assert len(refs) == 4, "the ingest schedule must outlast a slow grant"
    assert slept == [5, 15, 30], "waits escalate rather than giving up after one"


def test_retrying_stops_as_soon_as_the_source_answers(monkeypatch):
    slept = _no_sleep(monkeypatch)
    adapter = _LaggyAdapter(ready_on_call=1)

    refs = pipeline._list_documents_with_first_sync_retry(adapter, is_first_sync=True)

    assert len(refs) == 4
    assert slept == [], "a healthy first sync must never pay a retry wait"
    assert adapter.calls == 1


def test_a_re_sync_never_waits(monkeypatch):
    """A connection with a stored baseline can trust one listing."""
    slept = _no_sleep(monkeypatch)
    adapter = _LaggyAdapter(ready_on_call=99)  # always empty

    refs = pipeline._list_documents_with_first_sync_retry(
        adapter,
        is_first_sync=False,
        retry_delays=pipeline._FIRST_SYNC_INGEST_RETRY_DELAYS,
    )

    assert refs == []
    assert slept == []
    assert adapter.calls == 1
