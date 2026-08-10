"""Ingestion must be parallel where it waits, and observable while it runs.

Two independent complaints, one symptom ("sync takes minutes and the screen
never changes"):

1. **Slow.** ``contextualize_chunks`` issued one LLM call per chunk, strictly
   serially. A page of 10 chunks meant 10 sequential network round trips and a
   whole workspace meant hundreds — the wall clock was almost entirely waiting.
2. **Opaque.** The job row went ``queued`` -> ``running`` -> ``succeeded`` with
   ``doc_count`` written only at the very end, so every poll during those
   minutes returned byte-identical JSON. To the person watching, a working sync
   and a hung one looked exactly the same.

These tests pin both fixes: the calls actually overlap, and the job row
actually advances mid-run. They use fakes throughout — no LLM, no network.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass

import pytest
from cryptography.fernet import Fernet

from app.config.settings import ContextualSettings
from app.ingestion.contextualize import contextualize_chunks
from app.ingestion.pipeline import ingest_source
from app.sources.base import SourceDocument, SourceRef

from .conftest import requires_db


class _SlowLLM:
    """Records overlap: how many calls were in flight at the same moment."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        with self._lock:
            self.in_flight += 1
            self.calls += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        time.sleep(self.delay)
        with self._lock:
            self.in_flight -= 1
        return "CONTEXT"


def test_contextualization_calls_actually_overlap():
    """The whole point of the fix — without overlap it is still serial."""
    llm = _SlowLLM()
    chunks = [f"chunk {i}" for i in range(8)]

    contextualize_chunks(llm, "doc", chunks, concurrency=4)

    assert llm.calls == 8
    assert llm.max_in_flight > 1, "calls never overlapped — still effectively serial"
    assert llm.max_in_flight <= 4, "concurrency bound was not respected"


def test_parallel_contextualization_preserves_chunk_order():
    """Completion order must not become storage order.

    A chunk carries its position in the document; reordering them would silently
    corrupt what gets embedded, which no test of *speed* would ever catch.
    """

    class _Reverser:
        """Finishes late chunks first, so any order dependence shows up."""

        def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
            marker = prompt.rsplit("chunk-", 1)[-1].split("\n", 1)[0].strip()
            time.sleep(0.02 * (5 - int(marker)))
            return f"ctx-{marker}"

    chunks = [f"chunk-{i}" for i in range(5)]

    result = contextualize_chunks(_Reverser(), "doc", chunks, concurrency=5)

    assert [r.split("\n\n")[1] for r in result] == chunks
    assert [r.split("\n\n")[0] for r in result] == [f"ctx-{i}" for i in range(5)]


def test_a_transient_llm_failure_is_retried_rather_than_silently_dropped():
    """Best-effort must not mean "one blip costs this chunk its context".

    The degradation is invisible in the result — the chunk still stores fine,
    just without its retrieval context — so a transient 429 would quietly cost
    retrieval quality with nothing to show for it.
    """
    from app.core.exceptions import LLMProviderError

    class _FlakyOnce:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, prompt, *, max_tokens=None):
            self.calls += 1
            if self.calls == 1:
                raise LLMProviderError("rate limited")
            return "CONTEXT"

    llm = _FlakyOnce()

    out = contextualize_chunks(llm, "doc", ["body"], concurrency=1)

    assert llm.calls == 2
    assert out == ["CONTEXT\n\nbody"]


def test_a_quota_rejection_waits_the_window_the_server_named(monkeypatch):
    """A 429 is not a blip and must not be retried on the generic backoff.

    Observed live against Gemini's free tier: a hard 15 requests/minute, with
    the server itself asking for ~41s. Retrying that after 0.5s spends another
    request against the same exhausted budget — it makes the rate limiting worse
    *and* still ends in a silent quality loss.
    """
    from app.core.exceptions import LLMRateLimitError

    slept: list[float] = []
    monkeypatch.setattr("app.ingestion.contextualize.time.sleep", slept.append)

    class _RateLimitedOnce:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, prompt, *, max_tokens=None):
            self.calls += 1
            if self.calls == 1:
                raise LLMRateLimitError("429", retry_after=12.0)
            return "CONTEXT"

    out = contextualize_chunks(_RateLimitedOnce(), "doc", ["body"], concurrency=1)

    assert slept == [12.0], f"ignored the server's retry window: {slept}"
    assert out == ["CONTEXT\n\nbody"]


def test_a_quota_window_longer_than_we_will_stall_for_gives_up(monkeypatch):
    """Honouring the window must not let one chunk stall the whole run."""
    from app.core.exceptions import LLMRateLimitError

    slept: list[float] = []
    monkeypatch.setattr("app.ingestion.contextualize.time.sleep", slept.append)

    class _LongWait:
        def generate(self, prompt, *, max_tokens=None):
            raise LLMRateLimitError("429", retry_after=600.0)

    out = contextualize_chunks(_LongWait(), "doc", ["body"], concurrency=1)

    assert slept == [], "slept on a window we said we would not wait for"
    assert out == ["body"]


def test_a_persistently_failing_llm_still_degrades_to_the_raw_chunk():
    """Retries must not turn a bad endpoint into a failed ingest."""
    from app.core.exceptions import LLMProviderError

    class _AlwaysFails:
        def generate(self, prompt, *, max_tokens=None):
            raise LLMProviderError("endpoint down")

    out = contextualize_chunks(_AlwaysFails(), "doc", ["body"], concurrency=1)

    assert out == ["body"]


def test_concurrency_one_keeps_the_old_serial_behaviour():
    """The kill-switch has to genuinely serialize, not just cap the pool."""
    llm = _SlowLLM(delay=0.01)

    contextualize_chunks(llm, "doc", [f"c{i}" for i in range(5)], concurrency=1)

    assert llm.max_in_flight == 1


def test_concurrency_is_configurable_and_rejects_nonsense(monkeypatch):
    monkeypatch.setenv("INGEST_CONTEXTUAL_CONCURRENCY", "3")
    assert ContextualSettings.from_env().concurrency == 3

    # A zero/negative/garbage value must fall back to the default rather than
    # producing a pool of zero workers (which would deadlock the ingest).
    for bad in ("0", "-4", "many"):
        monkeypatch.setenv("INGEST_CONTEXTUAL_CONCURRENCY", bad)
        assert ContextualSettings.from_env().concurrency > 0


# -- progress reporting -------------------------------------------------------


@dataclass
class _FakeAdapter:
    pages: int

    def list_documents(self) -> list[SourceRef]:
        return [
            SourceRef(external_id=f"p{i}", title=f"Page {i}", last_modified=None)
            for i in range(self.pages)
        ]

    def fetch_document(self, external_id: str) -> SourceDocument:
        return SourceDocument(
            external_id=external_id,
            title=f"Title {external_id}",
            content=f"Body text for {external_id}. " * 20,
            source_uri=None,
            last_modified=None,
        )

    def get_last_modified(self, external_id: str):
        return None


class _FakeStore:
    def __init__(self):
        self.last_chunks: list[str] = []

    def list_source_documents(self, org_id, provider, workspace_id=None):
        return []

    def upsert_source_document(self, org_id, **kwargs):
        self.last_chunks = list(kwargs.get("chunks") or [])
        return "doc-id"

    def acknowledge_source_document(self, org_id, **kwargs):
        return None

    def delete_source_documents(self, org_id, provider, ids, workspace_id=None):
        return 0


class _FakeEmbedder:
    def embed(self, texts):
        return [[0.0] * 4 for _ in texts]


def test_ingest_reports_progress_as_each_document_completes():
    """The UI's spinner is only truthful if this fires mid-run, not just at the end."""
    seen: list[tuple[str, int, int]] = []

    result = ingest_source(
        _FakeAdapter(pages=4),
        "org-1",
        provider="notion",
        embedder=_FakeEmbedder(),
        store=_FakeStore(),
        contextual=ContextualSettings(enabled=False),
        on_progress=lambda phase, done, total: seen.append((phase, done, total)),
    )

    assert result.documents_added == 4

    # A phase is reported before the (slow) listing call, so the very first poll
    # after enqueue already shows something other than a bare "running".
    assert seen[0][0] == "listing"

    # Mid-document phases so the UI is not stuck at "0 of N" while page 1 embeds.
    assert ("preparing", 0, 4) in seen
    assert ("embedding", 0, 4) in seen

    # Completed-page ticks (processed advances only after each page is stored).
    indexing = [s for s in seen if s[0] == "indexing"]
    assert [done for _, done, _ in indexing] == [1, 2, 3, 4]
    assert all(total == 4 for _, _, total in indexing)


def test_a_failing_progress_sink_never_breaks_the_ingest():
    """Progress is observability. Losing it must not lose the sync."""

    def _explode(phase, done, total):
        raise RuntimeError("progress backend is down")

    result = ingest_source(
        _FakeAdapter(pages=2),
        "org-1",
        provider="notion",
        embedder=_FakeEmbedder(),
        store=_FakeStore(),
        contextual=ContextualSettings(enabled=False),
        on_progress=_explode,
    )

    assert result.documents_added == 2


# -- the round trip a poller actually sees ------------------------------------


@pytest.fixture
def _connected_org(store, org_cleanup, monkeypatch):
    from app.auth import OAuthTokens, save_connection

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    org_id = store.create_organization(f"Progress Org {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    connection_id = save_connection(
        org_id,
        "notion",
        OAuthTokens(
            access_token="ntn_fake",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="ws-progress",
        ),
    )
    return org_id, connection_id


@requires_db
def test_progress_is_visible_on_the_job_row_while_it_is_still_running(_connected_org):
    """The whole fix, from the poller's point of view.

    Before: two polls of a running job returned identical JSON for minutes.
    After: ``processed_documents`` advances, so the UI has something true to
    render and can tell "working" apart from "hung".
    """
    from app.api.serialize import job_payload
    from app.jobs import queue

    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)
    # claim_next() takes the globally-oldest queued job, which need not be ours
    # if another test left one behind — drain until we have it, so a failure
    # here means the progress fix broke, not that the queue was busy.
    for _ in range(20):
        claimed = queue.claim_next()
        if claimed is None or claimed.id == job_id:
            break
    assert queue.get_job(org_id, job_id).status == "running"

    queue.update_progress(job_id, phase="indexing", processed=0, total=17)
    first = job_payload(queue.get_job(org_id, job_id))

    queue.update_progress(job_id, processed=9)
    second = job_payload(queue.get_job(org_id, job_id))

    assert first["status"] == second["status"] == "running"
    assert first != second, "a running job still looks frozen to a poller"
    assert (first["processed_documents"], first["total_documents"]) == (0, 17)
    # Advancing the counter alone must not wipe the phase/total already set.
    assert (second["processed_documents"], second["total_documents"]) == (9, 17)
    assert second["phase"] == "indexing"


@requires_db
def test_a_job_that_never_reported_progress_still_serializes(_connected_org):
    """Old rows predate these columns — they must not 500 the jobs list."""
    from app.api.serialize import job_payload
    from app.jobs import queue

    org_id, connection_id = _connected_org
    job_id = queue.enqueue(org_id, connection_id)

    payload = job_payload(queue.get_job(org_id, job_id))

    assert payload["phase"] is None
    assert payload["total_documents"] is None
    assert payload["processed_documents"] == 0


def test_deferred_contextual_skips_llm_during_fast_sync():
    """Defer mode must not call the LLM while unlocking the product."""

    class _BoomLLM:
        def generate(self, prompt: str) -> str:
            raise AssertionError("LLM must not run during deferred fast sync")

    result = ingest_source(
        _FakeAdapter(pages=2),
        "org-1",
        provider="notion",
        embedder=_FakeEmbedder(),
        store=_FakeStore(),
        llm=_BoomLLM(),
        contextual=ContextualSettings(enabled=True, defer=True, concurrency=1),
    )
    assert result.documents_added == 2
    assert result.ingested_external_ids == ["p0", "p1"]


def test_enrich_source_contextual_rewrites_chunks_with_llm_prefix():
    from app.ingestion.pipeline import enrich_source_contextual

    class _PrefixLLM:
        def generate(self, prompt: str) -> str:
            return "Section: leave policy."

    store = _FakeStore()
    # Seed raw chunks via fast deferred ingest.
    ingest_source(
        _FakeAdapter(pages=1),
        "org-1",
        provider="notion",
        embedder=_FakeEmbedder(),
        store=store,
        contextual=ContextualSettings(enabled=True, defer=True),
    )
    n = enrich_source_contextual(
        _FakeAdapter(pages=1),
        "org-1",
        provider="notion",
        external_ids=["p0"],
        embedder=_FakeEmbedder(),
        store=store,
        llm=_PrefixLLM(),
        contextual=ContextualSettings(enabled=True, defer=True, concurrency=1),
    )
    assert n == 1
    # FakeStore keeps last upserted chunks on .last_chunks
    assert store.last_chunks
    assert store.last_chunks[0].startswith("Section: leave policy.")
