"""The cached payload must survive json.dumps and come back unchanged.

`RetrievedChunk.last_modified` is a datetime, and the cache is only written
when `conversation_id is None` -- which the web chat never does, because it
opens a conversation first. So a bare `asdict` raised `Object of type datetime
is not JSON serializable` for the first caller that passed None (the Slack
bot), on every answer, in production.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from app.rag import query_cache
from app.vectorstore.base import RetrievedChunk


def _chunk(last_modified):
    return RetrievedChunk(
        content="a thread", score=0.8, document_id="d1", chunk_index=0, org_id="o1",
        document_title="#eng: deploy", source_provider="slack",
        last_editor="ada", last_modified=last_modified,
    )


def test_a_chunk_with_a_datetime_is_json_serializable():
    when = datetime(2026, 9, 15, 11, 46, tzinfo=timezone.utc)
    payload = query_cache._chunk_to_dict(_chunk(when))
    json.dumps(payload)  # raised before the fix
    assert payload["last_modified"] == when.isoformat()


def test_the_datetime_round_trips_back_to_a_datetime():
    when = datetime(2026, 9, 15, 11, 46, tzinfo=timezone.utc)
    restored = query_cache._chunk_from_dict(
        json.loads(json.dumps(query_cache._chunk_to_dict(_chunk(when))))
    )
    assert restored.last_modified == when
    assert restored.document_title == "#eng: deploy"


def test_a_missing_timestamp_stays_none():
    restored = query_cache._chunk_from_dict(
        json.loads(json.dumps(query_cache._chunk_to_dict(_chunk(None))))
    )
    assert restored.last_modified is None


def test_an_unparseable_stored_timestamp_degrades_instead_of_raising():
    """One bad row must not poison every later read of that org's cache."""
    restored = query_cache._chunk_from_dict({
        "content": "x", "score": 0.5, "document_id": "d", "chunk_index": 0,
        "org_id": "o", "last_modified": "not-a-date",
    })
    assert restored.last_modified is None
