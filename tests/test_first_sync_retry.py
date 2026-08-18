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


def test_an_empty_listing_is_confirmed_before_it_deletes_anything(monkeypatch):
    """The live blip: 4 stored, one listing returns nothing, they're all still there."""
    slept = _no_sleep(monkeypatch)
    adapter = _LaggyAdapter(ready_on_call=1)  # re-list finds the 4 threads

    assert not pipeline._empty_listing_is_confirmed(
        adapter, stored_count=4, live_count=0
    )
    assert slept == [pipeline._EMPTY_LISTING_CONFIRM_DELAY_SECONDS]


def test_a_source_that_really_is_empty_still_gets_cleaned_up(monkeypatch):
    """Refusing every empty wipe would leave deleted content answering forever."""
    _no_sleep(monkeypatch)
    adapter = _LaggyAdapter(ready_on_call=99)  # empty on the second read too

    assert pipeline._empty_listing_is_confirmed(adapter, stored_count=4, live_count=0)


def test_a_failed_re_list_never_authorizes_a_wipe(monkeypatch):
    _no_sleep(monkeypatch)

    class _Broken:
        def list_documents(self):
            raise RuntimeError("rate limited")

    assert not pipeline._empty_listing_is_confirmed(
        _Broken(), stored_count=4, live_count=0
    )


def test_a_partial_removal_costs_no_second_opinion(monkeypatch):
    slept = _no_sleep(monkeypatch)
    adapter = _LaggyAdapter(ready_on_call=1)

    assert pipeline._empty_listing_is_confirmed(adapter, stored_count=4, live_count=3)
    assert slept == [], "only a total wipe is worth a re-list"
    assert adapter.calls == 0


def test_the_scale_guard_is_unchanged():
    removed, suspicious = pipeline._sanitize_removals(
        [str(i) for i in range(9)], stored_count=10
    )
    assert removed == []
    assert suspicious is True


def test_slack_threads_without_channel_prefix_are_queued_for_reindex():
    from app.sources.base import SourceRef
    from app.vectorstore.base import StoredSourceDocument

    ref = SourceRef(external_id="C1:1", title="#chan: hi")
    stored = {
        "C1:1": StoredSourceDocument(
            document_id="d1",
            provider="slack",
            external_id="C1:1",
            title="hi",  # old ingest: raw message, no #channel prefix
            source_uri=None,
            last_modified=None,
        )
    }
    to_update, unchanged = pipeline._reindex_slack_docs_missing_channel_prefix(
        [ref], stored, to_update=[], unchanged=1
    )
    assert [r.external_id for r in to_update] == ["C1:1"]
    assert unchanged == 0

    already_prefixed = StoredSourceDocument(
        document_id="d1",
        provider="slack",
        external_id="C1:1",
        title="#chan: hi",
        source_uri=None,
        last_modified=None,
    )
    to_update, unchanged = pipeline._reindex_slack_docs_missing_channel_prefix(
        [ref], {"C1:1": already_prefixed}, to_update=[], unchanged=1
    )
    assert to_update == []
    assert unchanged == 1
