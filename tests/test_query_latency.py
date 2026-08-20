"""Query-path latency work — behaviour must be identical, only cheaper.

Three independent costs on the read path, each measured before being changed:

1. **The corpus was refetched per question.** ``_normalize_for_retrieval``
   eagerly called ``list_chunk_texts`` — an unbounded ``SELECT content FROM
   chunks WHERE org_id = ...`` — while the normalizer caches its per-org
   dictionary for the life of the process. So every question after the first
   shipped the whole corpus over the wire and discarded it unread, and a
   decomposed question did it once *per sub-question*.
2. **Independent searches ran serially.** Vector and keyword for one query, and
   every sub-question's pair, are independent round trips that were issued one
   after another.
3. **The keyword search was unbounded.** Every chunk matching the tsquery came
   back with full content plus a computed cosine, to keep 30 of them.

These tests pin the *behaviour* (identical results), not the timings — a
wall-clock assertion would be flaky on shared CI. The speedups were measured
separately on a 400-chunk corpus: ~40% for one question, ~48% for three
sub-questions.
"""

from __future__ import annotations

import uuid
from collections import Counter

import pytest

from app.config.settings import QueryNormSettings
from app.rag.query_normalize import CorpusSpellNormalizer
from app.rag.retrieval import HybridRetriever
from app.vectorstore.base import RetrievedChunk

from .conftest import requires_db

_ON = QueryNormSettings(enabled=True)


# -- 1. the corpus is read once, not once per question ------------------------


def test_corpus_is_read_only_on_a_dictionary_cache_miss():
    """The whole point: the thunk must not be called on a cache hit."""
    calls = {"n": 0}

    def corpus():
        calls["n"] += 1
        return ["annual leave entitlement policy handbook"]

    norm = CorpusSpellNormalizer(_ON)
    for _ in range(5):
        norm.normalize("what is the anual leave", "org-1", corpus)

    assert calls["n"] == 1, "corpus was refetched despite a cached dictionary"


def test_each_org_still_builds_its_own_dictionary():
    """Laziness must not accidentally share one org's vocabulary with another."""
    seen: list[str] = []

    def corpus_for(org: str):
        def thunk():
            seen.append(org)
            return [f"{org}specificvocabulary term"]

        return thunk

    norm = CorpusSpellNormalizer(_ON)
    norm.normalize("hello there", "org-a", corpus_for("org-a"))
    norm.normalize("hello there", "org-b", corpus_for("org-b"))

    assert seen == ["org-a", "org-b"]


def test_a_plain_iterable_still_works():
    """Scripts and tests pass a list; that path must behave identically."""
    norm = CorpusSpellNormalizer(_ON)

    out = norm.normalize("what is the anual leave", "org-1", ["annual leave policy"])

    assert "annual" in out


def test_normalization_survives_a_store_that_cannot_list_chunks():
    """A store without the optional capability must degrade, not raise."""

    def exploding():
        raise NotImplementedError

    norm = CorpusSpellNormalizer(_ON)

    assert norm.normalize("anual leave", "org-1", exploding) == "anual leave"


# -- 2. concurrent first stage returns exactly what serial did ----------------


class _RecordingStore:
    """Fake store that records call order and returns deterministic hits."""

    def __init__(self) -> None:
        self.vector_calls: list[str] = []
        self.keyword_calls: list[str] = []

    def _hit(self, tag: str, i: int, score: float) -> RetrievedChunk:
        return RetrievedChunk(
            content=f"{tag}-{i}",
            score=score,
            document_id=f"doc-{tag}-{i}",
            chunk_index=i,
            org_id="org-1",
            document_title=tag,
        )

    def query(self, org_id, query_embedding, top_k=5, workspace_id=None, source_provider=None, date_range=None):
        tag = f"v{int(query_embedding[0])}"
        self.vector_calls.append(tag)
        return [self._hit(tag, i, 0.9 - i * 0.1) for i in range(3)]

    def keyword_search(
        self,
        org_id,
        query_text,
        query_embedding,
        top_k=30,
        workspace_id=None,
        source_provider=None,
        date_range=None,
    ):
        self.keyword_calls.append(query_text)
        return [self._hit(f"k-{query_text}", i, 0.5 - i * 0.1) for i in range(2)]


def _serial_equivalent(retriever, store, org_id, pairs, pool):
    """What the old sequential loop produced, for a direct comparison."""
    out = []
    for q_text, q_vec in pairs:
        v = store.query(org_id, q_vec, top_k=pool)
        k = store.keyword_search(org_id, q_text, q_vec, top_k=pool)
        out.append(retriever._rrf_fuse([v, k], retriever._settings.rrf_k))
    return out


def test_concurrent_first_stage_matches_the_serial_result_exactly():
    """RRF fusion is order-sensitive, so completion order must not leak in."""
    store = _RecordingStore()
    retriever = HybridRetriever(store, reranker=None)
    pairs = [("first question", [1.0]), ("second question", [2.0]), ("third", [3.0])]

    concurrent = retriever._first_stage_all("org-1", pairs, 30)
    expected = _serial_equivalent(retriever, store, "org-1", pairs, 30)

    assert [[c.document_id for c in lst] for lst in concurrent] == [
        [c.document_id for c in lst] for lst in expected
    ]


def test_every_sub_question_is_searched_exactly_once():
    """Concurrency must not drop or duplicate a sub-question."""
    store = _RecordingStore()
    retriever = HybridRetriever(store, reranker=None)
    pairs = [("alpha", [1.0]), ("beta", [2.0]), ("gamma", [3.0])]

    retriever._first_stage_all("org-1", pairs, 30)

    assert sorted(store.keyword_calls) == ["alpha", "beta", "gamma"]
    assert len(store.vector_calls) == 3


def test_a_store_without_keyword_search_still_returns_vector_hits():
    """The NotImplementedError fallback has to survive the move into a thread."""

    class _VectorOnly(_RecordingStore):
        def keyword_search(self, *a, **kw):
            raise NotImplementedError

    retriever = HybridRetriever(_VectorOnly(), reranker=None)

    lists = retriever._first_stage_all("org-1", [("q", [1.0])], 30)

    assert len(lists) == 1 and lists[0], "vector hits were lost with keyword search absent"


# -- 3. the keyword search is bounded ----------------------------------------


@requires_db
def test_keyword_search_respects_the_candidate_limit(store, org_cleanup, embedder):
    """A common term must not pull the whole corpus back to return top_k.

    Measured before the fix on a 400-chunk corpus: the term "leave" fetched 160
    rows to return 30, a ratio that grows linearly with corpus size.
    """
    from app.config.settings import DatabaseSettings
    from app.vectorstore.pgvector_store import PgVectorStore

    org_id = store.create_organization(f"KW Limit {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    chunks = [f"annual leave policy section {i} for department {i}" for i in range(25)]
    store.upsert_source_document(
        org_id,
        provider="test",
        external_id="doc-1",
        title="Leave",
        chunks=chunks,
        embeddings=embedder.embed(chunks),
        source_uri=None,
        last_modified=None,
    )

    base = DatabaseSettings.from_env()
    capped = PgVectorStore(
        settings=DatabaseSettings(
            url=base.url,
            embedding_dim=base.embedding_dim,
            pool_min_size=base.pool_min_size,
            pool_max_size=base.pool_max_size,
            keyword_candidate_limit=5,
        )
    )
    vec = embedder.embed(["annual leave"])[0]

    hits = capped.keyword_search(org_id, "leave", vec, top_k=30)

    assert 0 < len(hits) <= 5, "candidate limit did not bound the result set"


@requires_db
def test_a_generous_limit_leaves_results_unchanged(store, org_cleanup, embedder):
    """At realistic corpus sizes the cap must be a genuine no-op."""
    from app.config.settings import DatabaseSettings
    from app.vectorstore.pgvector_store import PgVectorStore

    org_id = store.create_organization(f"KW NoOp {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    chunks = [f"annual leave policy section {i}" for i in range(12)]
    store.upsert_source_document(
        org_id,
        provider="test",
        external_id="doc-1",
        title="Leave",
        chunks=chunks,
        embeddings=embedder.embed(chunks),
        source_uri=None,
        last_modified=None,
    )
    vec = embedder.embed(["annual leave"])[0]
    base = DatabaseSettings.from_env()

    def hits_with(limit: int):
        s = PgVectorStore(
            settings=DatabaseSettings(
                url=base.url,
                embedding_dim=base.embedding_dim,
                pool_min_size=base.pool_min_size,
                pool_max_size=base.pool_max_size,
                keyword_candidate_limit=limit,
            )
        )
        return [(h.document_id, h.chunk_index) for h in s.keyword_search(
            org_id, "leave", vec, top_k=30
        )]

    assert hits_with(2000) == hits_with(100000)


# -- 4. the query is embedded once, not twice --------------------------------


class _CountingEmbedder:
    """Wraps a real embedder and records every call's inputs."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return self._inner.embed(texts)


@requires_db
def test_a_question_is_embedded_exactly_once(store, org_cleanup, embedder):
    """``_run`` embeds the normalized question for the Phase 8 reuse check, and
    ``_retrieve_for_subquestions`` used to embed the *identical string* again.

    A single BGE-M3 encode measures ~38ms — the most expensive CPU step on the
    query path — so this was ~38ms of duplicate work on every non-decomposed
    question. It is invisible inside either function; only counting calls across
    a whole request reveals it, which is why the regression test is shaped this
    way rather than asserting on a timing.

    The assertion is "no *text* is embedded twice" rather than "exactly one
    single-item embed". The first version of this test used the latter and was
    fragile: when the LLM endpoint rate-limits, generation can look insufficient
    and trigger the bounded recovery path, which legitimately embeds *different*
    single-item queries. Counting single-item calls conflated that real extra
    work with the duplicate work being guarded against. Re-embedding the same
    string is the actual defect, so that is what is asserted.
    """
    from app.rag.factory import build_rag_pipeline

    org_id = store.create_organization(f"Embed Once {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    chunks = ["Full-time employees receive 25 days of paid annual leave a year."]
    store.upsert_source_document(
        org_id,
        provider="test",
        external_id="doc-1",
        title="Annual Leave",
        chunks=chunks,
        embeddings=embedder.embed(chunks),
        source_uri=None,
        last_modified=None,
    )

    counting = _CountingEmbedder(embedder)
    pipeline = build_rag_pipeline(
        embedder=counting, store=store, memory=None, web_search=None
    )

    pipeline.answer("How many annual leave days do full-time employees get?", org_id)

    embedded: Counter[str] = Counter()
    for call in counting.calls:
        embedded.update(call)

    repeats = {text: n for text, n in embedded.items() if n > 1}
    assert not repeats, f"the same text was embedded more than once: {repeats}"


# -- 5. the grounded prompt stays cacheable ----------------------------------


def test_the_grounded_prompt_keeps_a_stable_cacheable_prefix():
    """CONTEXT and QUESTION must stay LAST in the prompt.

    Measured: the grounded prompt is ~2,319 tokens for a 5-chunk question, of
    which ~2,219 (96%) is fixed instruction scaffold resent on every question.
    That is only cheap if a provider can cache it, and a provider can only cache
    a byte-identical *prefix*. Today 98% of the string is shared across
    different questions because the variable parts are appended at the end.

    Moving CONTEXT or QUESTION earlier would collapse the cacheable prefix and
    silently make every question pay full price — invisible in any output, which
    is exactly why it needs a test rather than a comment.
    """
    from app.rag.prompts import build_grounded_prompt

    a = build_grounded_prompt("How many leave days?", ["chunk about leave"], "fb")
    b = build_grounded_prompt("What is the dress code?", ["chunk about dress"], "fb")

    shared = 0
    while shared < min(len(a), len(b)) and a[shared] == b[shared]:
        shared += 1

    assert shared / len(a) > 0.90, (
        f"cacheable prefix collapsed to {shared / len(a):.0%} of the prompt — "
        "did CONTEXT or QUESTION move earlier?"
    )
    # The whole instruction scaffold — up to and including the CONTEXT header —
    # must fall INSIDE the shared prefix. Anything question-specific appearing
    # before it would end the cacheable region early.
    assert "CONTEXT:" in a[:shared], (
        "the prompt diverges before the CONTEXT header, so the instruction "
        "scaffold is no longer a stable cacheable prefix"
    )
    # (The literal word "QUESTION" appears throughout the rules, so checking for
    # it proves nothing — check the actual question text instead.)
    assert "How many leave days?" not in a[:shared], (
        "the question text appears inside the shared prefix — the prompt would "
        "need rebuilding per question with nothing cacheable after it"
    )
