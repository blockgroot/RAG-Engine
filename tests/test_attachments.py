"""In-chat file attachments: extraction bounds, privacy scoping, routing override.

No DB and no LLM: the store is exercised through a fake connection and the
pipeline through a fake LLM, because the point of these tests is the RULES
(what is bounded, who can read it, what overrides routing), and those are
decided in Python, not in Postgres.
"""

from __future__ import annotations

import csv
import io

import pytest

from app.attachments import extract as ex


# --- extraction is bounded, and says so --------------------------------------


def _csv_bytes(rows: int) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "amount"])
    for i in range(rows):
        w.writerow([f"row{i}", i])
    return buf.getvalue().encode()


def test_csv_over_the_row_cap_is_marked_truncated():
    text, truncated = ex.extract_text(
        "sales.csv", _csv_bytes(50), max_chars=10_000, max_pdf_pages=10, max_csv_rows=5
    )
    assert truncated is True
    assert len(text.splitlines()) == 5


def test_a_small_csv_is_not_marked_truncated():
    text, truncated = ex.extract_text(
        "sales.csv", _csv_bytes(3), max_chars=10_000, max_pdf_pages=10, max_csv_rows=50
    )
    assert truncated is False
    assert "name | amount" in text


def test_the_char_cap_truncates_and_is_reported():
    text, truncated = ex.extract_text(
        "notes.txt", b"x" * 500, max_chars=100, max_pdf_pages=10, max_csv_rows=10
    )
    assert truncated is True
    assert len(text) == 100


def test_an_unreadable_type_is_refused_by_name():
    with pytest.raises(ex.AttachmentError) as err:
        ex.extract_text(
            "photo.heic", b"...", max_chars=10, max_pdf_pages=1, max_csv_rows=1
        )
    assert "photo.heic" in str(err.value)


def test_a_file_with_no_text_layer_says_so_rather_than_returning_empty():
    """A silent empty extraction reads as 'this file is blank'."""
    with pytest.raises(ex.AttachmentError) as err:
        ex.extract_text(
            "scan.txt", b"   \n\n  ", max_chars=100, max_pdf_pages=1, max_csv_rows=1
        )
    assert "scan.txt" in str(err.value)


def test_kind_is_selected_by_extension_not_content_type():
    """Browsers send application/octet-stream for .docx routinely."""
    assert ex.kind_for("contract.DOCX") == "docx"
    assert ex.kind_for("a.pdf") == "pdf"
    assert ex.kind_for("noextension") is None


# --- the store scopes every read on (conversation, org, user) ----------------


def test_every_store_query_filters_on_all_three_owners(monkeypatch):
    """A missing predicate here LEAKS rather than fails, so it is pinned."""
    from app.attachments import store

    seen: list[str] = []

    class _Conn:
        def execute(self, sql, params):
            seen.append(" ".join(sql.split()))
            self._rows = []
            return self

        def fetchall(self):
            return []

        def fetchone(self):
            return [0]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(store, "get_connection", lambda: _Conn())
    kw = {"org_id": "o", "conversation_id": "c", "user_id": "u"}
    store.list_attachments(**kw)
    store.load_attachment_texts(**kw)
    store.count_attachments(**kw)
    store.delete_attachment(attachment_id="a", **kw)

    assert len(seen) == 4
    for sql in seen:
        assert "conversation_id = %s" in sql, sql
        assert "org_id = %s" in sql, sql
        assert "user_id = %s" in sql, sql


# --- an attachment overrides routing and reuses the strict prompt ------------


def _pipeline(llm):
    """Same shape as `test_audit.py`'s fixture -- retrieval is irrelevant here,
    because the attachment path never touches it."""
    from app.config.settings import RagSettings, RecoverySettings, ReuseSettings
    from app.rag.pipeline import RagPipeline
    from .fakes import KeywordEmbedder, RecordingVectorStore

    return RagPipeline(
        llm=llm,
        embedder=KeywordEmbedder(),
        store=RecordingVectorStore("org", content="unused"),
        settings=RagSettings(top_k=3, similarity_threshold=0.1, fallback_response=FALLBACK),
        memory=None,
        web_search=None,
        retriever=None,
        reuse_settings=ReuseSettings(enabled=False),
        recovery_settings=RecoverySettings(enabled=False),
    )


FALLBACK = "I don't have information on that in the available policy documents."
ANSWER = "MODE: A\n\nThe notice period is 30 days."


def test_attachment_text_reaches_the_prompt_fenced_and_named(monkeypatch):
    from app.rag import prompts

    built: dict = {}
    real = prompts.build_grounded_prompt

    def _spy(**kwargs):
        built.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr("app.rag.pipeline.build_grounded_prompt", _spy)

    from .fakes import RecordingLLM

    llm = RecordingLLM(answer=ANSWER)
    pipeline = _pipeline(llm)
    result = pipeline.answer_from_attachments(
        "What is the notice period?",
        [("contract.pdf", "Notice period: 30 days.", False)],
        org_id="org",
    )

    assert result.answered is True
    assert result.source == "attachment"
    # The filename rides IN the context, so "which file said that?" is answerable.
    assert "Attached file: contract.pdf" in built["contexts"][0]
    # The attachment profile, not the agent's -- a wrong escalation hint sends
    # someone to a team that has never seen their upload.
    assert built["profile"].source_label == "attachment"
    # Fenced as untrusted, exactly like a synced document.
    assert any("UNTRUSTED_DOCUMENT_CONTENT" in p for p in llm.prompts)


def test_a_truncated_attachment_says_so_in_the_prompt():
    from .fakes import RecordingLLM

    llm = RecordingLLM(answer=ANSWER)
    pipeline = _pipeline(llm)
    pipeline.answer_from_attachments(
        "summarise this", [("big.pdf", "page one text", True)], org_id="org"
    )
    assert any("truncated" in p for p in llm.prompts)


def test_an_attachment_with_no_usable_text_refuses_without_calling_the_llm():
    from .fakes import RecordingLLM

    llm = RecordingLLM(answer=ANSWER)
    pipeline = _pipeline(llm)
    result = pipeline.answer_from_attachments(
        "what is this?", [("empty.txt", "   ", False)], org_id="org"
    )
    assert result.answered is False
    assert result.source == "none"
    assert llm.prompts == []


# --- long files are PAGED, not truncated -------------------------------------

from app.config.settings import AttachmentSettings  # noqa: E402
from app.llm.base import ChatResult, ToolCall  # noqa: E402
from app.rag import attachment_tools as at  # noqa: E402

PAGING = AttachmentSettings(
    inline_char_budget=100,
    preview_chars=40,
    max_read_chars=500,
    max_reads=3,
)
LONG = "HEAD. " + ("filler. " * 200) + " NOTICE PERIOD IS 30 DAYS. " + ("tail. " * 200)


class _ToolLLM:
    """A RecordingLLM that can also answer a tool round with fixed calls."""

    model = "tool-test"

    def __init__(self, calls, answer=None):
        self._calls = calls
        self._answer = answer or ANSWER
        self.prompts: list[str] = []
        self.tool_prompts: list[str] = []
        self.offered_tools: list = []

    def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self._answer

    def generate_with_tools(self, messages, tools=None, tool_choice=None, timeout=None):
        self.tool_prompts.append(messages[0]["content"])
        self.offered_tools = tools or []
        return ChatResult(text=None, tool_calls=list(self._calls))


def _call(file_id, start, num):
    import json as _json

    return ToolCall(
        id="t1",
        name="read_file",
        arguments=_json.dumps(
            {"file_id": file_id, "start_char": start, "num_chars": num}
        ),
    )


def test_a_long_file_is_paged_and_the_read_section_reaches_the_prompt():
    llm = _ToolLLM([_call(1, LONG.index("NOTICE") - 20, 200)])
    pipeline = _pipeline(llm)
    result = pipeline.answer_from_attachments(
        "What is the notice period?",
        [("contract.pdf", LONG, False)],
        org_id="org",
        settings=PAGING,
    )
    assert result.answered is True
    # The section the model asked for is what got grounded on -- not the head.
    assert any("NOTICE PERIOD IS 30 DAYS" in x for x in llm.prompts)
    assert "read_file" in [t["function"]["name"] for t in llm.offered_tools]


def test_the_preview_states_the_length_so_offsets_are_not_guesswork():
    llm = _ToolLLM([_call(1, 0, 100)])
    pipeline = _pipeline(llm)
    pipeline.answer_from_attachments(
        "what is this?", [("contract.pdf", LONG, False)], org_id="org", settings=PAGING
    )
    assert f"{len(LONG)} characters" in llm.tool_prompts[0]
    # A preview, not the file: the point of paging is that this stays small.
    assert len(llm.tool_prompts[0]) < len(LONG)


def test_a_read_states_the_RANGE_so_an_excerpt_is_not_read_as_the_whole_file():
    llm = _ToolLLM([_call(1, 60, 120)])
    pipeline = _pipeline(llm)
    pipeline.answer_from_attachments(
        "summarise", [("contract.pdf", LONG, False)], org_id="org", settings=PAGING
    )
    assert any(f"characters 60–180 of {len(LONG)}" in x for x in llm.prompts)


def test_a_short_file_skips_the_tool_round_entirely():
    llm = _ToolLLM([])
    pipeline = _pipeline(llm)
    pipeline.answer_from_attachments(
        "what is this?", [("note.txt", "Short note.", False)], org_id="org",
        settings=PAGING,
    )
    assert llm.tool_prompts == []
    assert any("Short note." in x for x in llm.prompts)


def test_a_provider_without_tool_support_degrades_to_the_file_head():
    """The model picker spans backends, so this is routine, not a defect."""

    class _NoTools(_ToolLLM):
        def generate_with_tools(self, *a, **k):
            raise NotImplementedError("no tools here")

    llm = _NoTools([])
    pipeline = _pipeline(llm)
    result = pipeline.answer_from_attachments(
        "what is this?", [("contract.pdf", LONG, False)], org_id="org", settings=PAGING
    )
    assert result.answered is True
    assert any("HEAD." in x for x in llm.prompts)


def test_no_tool_call_degrades_to_the_head_rather_than_refusing():
    llm = _ToolLLM([])
    pipeline = _pipeline(llm)
    result = pipeline.answer_from_attachments(
        "what is this?", [("contract.pdf", LONG, False)], org_id="org", settings=PAGING
    )
    assert result.answered is True
    assert any("HEAD." in x for x in llm.prompts)


# --- untrusted tool arguments are absorbed, never fatal ----------------------


def test_nonsense_read_arguments_are_skipped_not_raised():
    files = [at.AttachedFile("a.txt", "abcdefghij", False)]
    out = at.run_reads(
        files,
        [
            ("read_file", {"file_id": 99, "start_char": 0, "num_chars": 5}),
            ("read_file", {"file_id": "x", "start_char": 0, "num_chars": 5}),
            ("read_file", {"file_id": 1, "start_char": 500, "num_chars": 5}),
            ("other_tool", {"file_id": 1}),
            ("read_file", {"file_id": 1, "start_char": 2, "num_chars": 3}),
        ],
        max_reads=10,
        max_read_chars=16000,
    )
    assert len(out) == 1
    assert "cde" in out[0]


def test_a_read_cannot_exceed_the_per_call_ceiling():
    files = [at.AttachedFile("a.txt", "x" * 50_000, False)]
    out = at.run_reads(
        files,
        [("read_file", {"file_id": 1, "start_char": 0, "num_chars": 999_999})],
        max_reads=4,
        max_read_chars=100,
    )
    assert "characters 0–100 of 50000" in out[0]


def test_the_number_of_reads_is_bounded():
    files = [at.AttachedFile("a.txt", "abcdefghij" * 10, False)]
    calls = [("read_file", {"file_id": 1, "start_char": i, "num_chars": 5}) for i in range(10)]
    out = at.run_reads(files, calls, max_reads=3, max_read_chars=100)
    assert len(out) == 3


# --- attachment text must not accumulate forever -----------------------------


def test_the_purge_runs_two_clocks_and_returns_a_count(monkeypatch):
    """Nothing in this codebase deletes a conversation, so the ON DELETE
    CASCADE never fires in practice and this sweep is the real cleanup."""
    from app.attachments import store

    seen: dict = {}

    class _Conn:
        def execute(self, sql, params):
            seen["sql"] = " ".join(sql.split())
            seen["params"] = params
            return self

        def fetchall(self):
            return [(1,), (1,), (1,)]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(store, "get_connection", lambda: _Conn())
    assert store.purge_expired_attachments(720, 24) == 3
    assert "DELETE FROM conversation_attachments" in seen["sql"]
    # Clock 1: everything, eventually.
    assert "a.created_at < now() - make_interval(hours => %s)" in seen["sql"]
    # Clock 2: sooner, when the conversation never asked anything -- which is
    # what a browser refresh leaves behind on this frontend.
    assert "NOT EXISTS ( SELECT 1 FROM conversation_turns" in seen["sql"]
    assert seen["params"] == (720, 24)


def test_the_unused_clock_is_far_shorter_than_the_general_one():
    """An unreachable file must not outlive the chat by weeks."""
    from app.attachments import store

    assert (
        store.DEFAULT_UNUSED_ATTACHMENT_TTL_HOURS
        < store.DEFAULT_ATTACHMENT_TTL_HOURS
    )
    # ...but long enough that someone still typing their first question keeps
    # the file they just uploaded.
    assert store.DEFAULT_UNUSED_ATTACHMENT_TTL_HOURS >= 12


def test_the_tick_reports_the_purge_and_survives_it_failing(monkeypatch):
    """One broken sweep must not abort syncing, facts or schedulers."""
    from app.jobs import worker

    monkeypatch.setattr(worker.queue, "reap_stuck", lambda: 0)
    monkeypatch.setattr(worker, "run_sync_tick", lambda: 0)
    monkeypatch.setattr(worker, "run_facts_tick", lambda: 0)
    monkeypatch.setattr(worker, "backfill_all_document_facts", lambda: 0)
    monkeypatch.setattr(worker, "run_scheduler_tick", lambda *a: 7)

    import app.attachments as attachments_pkg

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(attachments_pkg, "purge_expired_attachments", _boom)
    out = worker.run_external_tick()
    assert out["attachments_purged"] == 0
    assert out["schedulers_ran"] == 7
