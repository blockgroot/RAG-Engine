"""Retrieved context must carry its provenance to the model.

"Who wrote this?", "when was it last updated?" and "which app is this from?"
are among the most common things anyone asks about a document, and every answer
was already sitting on the `documents` row each hit JOINs. Only the title used
to reach the prompt, so all three refused against data we already had.

No DB, no network: the subject is the string handed to the prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.rag.context_assemble import assemble_context_texts, describe_hit


@dataclass
class Hit:
    content: str = "Full-time staff get 25 days of paid leave."
    document_title: str | None = "Leave Policy"
    source_provider: str | None = "notion"
    last_editor: str | None = "Ada Lovelace"
    last_modified: datetime | None = datetime(2026, 8, 12, tzinfo=timezone.utc)


def test_the_header_names_the_document_app_editor_and_date():
    text = describe_hit(Hit())
    assert "Leave Policy" in text
    assert "Notion" in text
    assert "Ada Lovelace" in text
    assert "12 Aug 2026" in text
    assert "Full-time staff get 25 days" in text


def test_the_provider_is_named_for_a_person_not_for_a_database():
    """A member asked about a Notion page, not about a "notion" string."""
    assert "Google Drive" in describe_hit(Hit(source_provider="google"))
    assert "notion" not in describe_hit(Hit())


def test_an_unknown_editor_is_omitted_not_rendered_as_unknown():
    """A placeholder in the context invites the model to answer "who wrote
    this?" with it. Crediting nobody is better than crediting "Unknown"."""
    text = describe_hit(Hit(last_editor=None))
    assert "Unknown" not in text
    assert "last edited by" not in text
    assert "Leave Policy" in text


def test_a_missing_date_is_omitted():
    text = describe_hit(Hit(last_modified=None))
    assert "updated" not in text
    assert "Leave Policy" in text


def test_a_chunk_with_no_metadata_at_all_is_passed_through_unchanged():
    """Legacy rows, the reuse path and test fakes carry none of this. They must
    not gain an empty "(From: )" header."""
    bare = Hit(document_title=None, source_provider=None, last_editor=None,
               last_modified=None)
    assert describe_hit(bare) == bare.content


def test_the_content_is_never_altered_by_the_header():
    """The header is prepended, never woven in -- the chunk text is what the
    gate scored and what the answer must be grounded in."""
    hit = Hit(content="Deploy is frozen till Monday.")
    assert hit.content in describe_hit(hit)


def test_the_header_survives_the_context_budget():
    """`assemble_context_texts` truncates on a char budget. A header that got
    cut off would leave a half-written attribution, which is worse than none."""
    hits = [describe_hit(Hit()) for _ in range(3)]
    out = assemble_context_texts(hits, max_chars=10_000)
    assert len(out) == 3
    assert all("Ada Lovelace" in text for text in out)


def test_a_hit_object_missing_the_new_fields_does_not_crash():
    """The reuse path reconstructs hits from conversation memory, which stores
    only content plus a locator. It must degrade, not raise."""
    class Old:
        content = "text"
        document_title = "Doc"

    assert describe_hit(Old()) == "(From: Doc)\ntext"
