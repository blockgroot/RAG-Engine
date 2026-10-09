from __future__ import annotations

from app.api.chat import _with_progress
from app.rag import progress
from app.vectorstore.base import RetrievedChunk


def _hit(doc, title, provider, editor=None):
    return RetrievedChunk(content="x", score=0.8, document_id=doc, chunk_index=0, org_id="o",
                          document_title=title, source_provider=provider, last_editor=editor)


def test_found_names_real_titles_per_app_and_the_deep_read():
    lines: list[str] = []
    token = progress.use_progress(lines.append)
    try:
        progress.searching("notion", {"notion", "slack"})
        progress.found([
            _hit("a", "Leave policy", "notion"), _hit("a", "Leave policy", "notion"),
            _hit("b", "#general", "slack", "Sana"), None,
        ], deep_docs=3)
    finally:
        progress.reset_progress(token)
    assert lines == [
        "Searching Notion and Slack",
        "Found in Notion: “Leave policy”",
        "Found in Slack: “#general” from Sana",
        "Reading the top 2 documents in full",
    ]


def test_no_listener_costs_nothing():
    progress.report("nobody hears this")  # no sink set: must not raise


def test_stream_carries_status_lines_before_the_answer_and_reraises():
    def body():
        progress.report("Searching Notion")
        yield "event: done\n\n"

    assert list(_with_progress(body)) == [
        'event: status\ndata: "Searching Notion"\n\n', "event: done\n\n"]

    def broken():
        raise RuntimeError("boom")
        yield  # pragma: no cover

    try:
        list(_with_progress(broken))
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("the worker's error must reach the stream")


def test_deep_hint_only_when_reading_deeper_could_help():
    from types import SimpleNamespace as R

    from app.api.chat import _deep_would_help

    refused = dict(grounded=False, access_restricted=False)
    assert _deep_would_help(R(top_score=0.6, **refused))  # relevant documents were found
    assert not _deep_would_help(R(top_score=0.1, **refused))  # gate miss: nothing to read
    assert not _deep_would_help(R(top_score=0.6, grounded=True, access_restricted=False))
    assert not _deep_would_help(R(top_score=0.6, grounded=False, access_restricted=True))
    # An upload longer than normal Ask reads whole; a short one is already read whole.
    assert _deep_would_help(R(top_score=None, **refused), [("big.pdf", "x" * 20_000, False)])
    assert not _deep_would_help(R(top_score=None, **refused), [("small.txt", "x" * 500, False)])


def test_deep_hint_on_a_partial_answer():
    from types import SimpleNamespace as R

    from app.api.chat import _deep_would_help

    base = dict(grounded=True, access_restricted=False, top_score=0.6)
    partial = "The budget is $2.4M. [1]\nNot covered: which customers are in wave 2."
    assert _deep_would_help(R(answer=partial, **base))
    assert not _deep_would_help(R(answer="The budget is $2.4M. [1]", **base))
