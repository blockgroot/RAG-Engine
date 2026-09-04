"""An indexed Linear issue must carry what people ask about it.

Before this, the document was description + comments only: no identifier, no
state, no assignee. So "what's the status of ENG-142?", "who's it assigned
to?" and "is it done?" all refused -- against fields the adapter was ALREADY
fetching for the activity feed, one query away.
"""

from __future__ import annotations

from app.sources.linear import _issue_preamble, _issue_title

ISSUE = {
    "id": "uuid-1",
    "identifier": "ENG-142",
    "title": "Fix login redirect",
    "state": {"name": "In Progress", "type": "started"},
    "assignee": {"name": "Priya"},
    "team": {"name": "Engineering"},
    "priorityLabel": "High",
    "labels": {"nodes": [{"name": "bug"}, {"name": "auth"}]},
}


def test_the_title_carries_the_identifier_people_actually_use():
    """Nobody asks about "Fix login redirect"; they ask about ENG-142."""
    assert _issue_title(ISSUE) == "ENG-142 - Fix login redirect"


def test_a_missing_identifier_falls_back_to_the_bare_title():
    assert _issue_title({"title": "Fix login redirect"}) == "Fix login redirect"
    assert _issue_title({}) == "Untitled issue"


def test_the_preamble_states_status_assignee_team_priority_and_labels():
    text = _issue_preamble(ISSUE)
    assert "ENG-142" in text
    assert "status In Progress" in text
    assert "assigned to Priya" in text
    assert "team Engineering" in text
    assert "priority High" in text
    assert "bug" in text and "auth" in text


def test_an_unset_assignee_is_omitted_not_described_as_unassigned():
    """Retrieval would answer "who is this assigned to?" with a word we
    invented. Saying nothing lets the strict prompt refuse instead."""
    text = _issue_preamble({**ISSUE, "assignee": None})
    assert "assigned to" not in text
    assert "unassigned" not in text.lower()
    assert "status In Progress" in text


def test_an_issue_with_nothing_but_an_id_still_produces_valid_prose():
    """A brand-new issue has no state, assignee or labels. The preamble must
    not become a trailing pile of punctuation."""
    text = _issue_preamble({"identifier": "ENG-1"})
    assert text == "Linear issue ENG-1."


def test_the_preamble_is_prose_not_a_table():
    """The embedder scores prose. A pipe-delimited row embeds badly and reads
    badly inside a chunk."""
    text = _issue_preamble(ISSUE)
    assert "|" not in text
    assert "\n" not in text


def test_the_graphql_query_asks_for_every_field_the_preamble_renders():
    """If a field leaves the query, the preamble silently drops it and every
    status question starts refusing again -- with no error anywhere."""
    from app.sources.linear import _ISSUE_QUERY

    for field in ("identifier", "state", "assignee", "team", "priorityLabel", "labels"):
        assert field in _ISSUE_QUERY, field


def test_the_listing_query_asks_for_the_identifier():
    from app.sources.linear import _ISSUES_QUERY

    assert "identifier" in _ISSUES_QUERY
