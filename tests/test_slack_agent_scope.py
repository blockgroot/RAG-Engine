"""Slack Agent: the ``source_provider`` retrieval filter and its chips.

The point of a separate ``SlackAgent`` is that a tab labelled "Slack" answers
only from Slack. That guarantee lives in one place — the store-level
``source_provider`` filter — so it is proven here directly against Postgres,
in the same spirit as ``test_isolation.py`` proves ``org_id`` scoping.

Note what this filter is and is not: it is a *relevance* boundary, not an
access one. Everything it can reach was already inside the caller's
``org_id``/``workspace_id``, which remain the only isolation guarantees.
"""

from __future__ import annotations

import uuid

from app.api.suggestions import build_slack_suggestions
from app.rag.query_cache import _question_hash

from .conftest import requires_db


def _seed(store, embedder, org_id: str, provider: str, title: str, text: str) -> None:
    store.upsert_source_document(
        org_id,
        provider=provider,
        external_id=f"{provider}-{uuid.uuid4().hex[:8]}",
        title=title,
        chunks=[text],
        embeddings=embedder.embed([text]),
        source_uri=None,
        last_modified=None,
    )


@requires_db
def test_slack_scoped_query_never_returns_another_providers_chunks(
    store, embedder, org_cleanup
):
    org_id = store.create_organization(f"Slack Scope {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    # Same topic in both corpora on purpose: if the filter were missing, the
    # Notion chunk is exactly what a "what's the parental leave policy" query
    # would surface, and the caller could not tell from the answer text.
    _seed(
        store,
        embedder,
        org_id,
        "notion",
        "Leave Policy",
        "Parental leave is 18 weeks of fully paid time off for all employees.",
    )
    _seed(
        store,
        embedder,
        org_id,
        "slack",
        "#people thread",
        "Parental leave came up again in standup; someone asked if it is 18 weeks.",
    )

    question = embedder.embed(["parental leave"])[0]

    slack_only = store.query(org_id, question, top_k=10, source_provider="slack")
    assert slack_only, "expected the Slack thread to be retrievable at all"
    assert all("thread" in (h.document_title or "") for h in slack_only), (
        "a Slack-scoped query returned a chunk from another provider"
    )

    unfiltered = store.query(org_id, question, top_k=10)
    titles = {h.document_title for h in unfiltered}
    assert "Leave Policy" in titles, (
        "the unfiltered query must be unchanged — this parameter is additive"
    )


@requires_db
def test_slack_scoped_keyword_search_is_filtered_too(store, embedder, org_cleanup):
    """The BM25 leg needs the same filter, or hybrid search leaks around it."""
    org_id = store.create_organization(f"Slack KW Scope {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    _seed(
        store,
        embedder,
        org_id,
        "notion",
        "Expenses Policy",
        "Reimbursement for travel expenses requires a receipt within 30 days.",
    )
    _seed(
        store,
        embedder,
        org_id,
        "slack",
        "#finance thread",
        "Reimbursement question: does the receipt rule apply to taxis?",
    )

    vec = embedder.embed(["reimbursement receipt"])[0]
    hits = store.keyword_search(
        org_id, "reimbursement receipt", vec, top_k=10, source_provider="slack"
    )
    assert hits, "expected the Slack thread via keyword search"
    assert all("thread" in (h.document_title or "") for h in hits)


def test_cache_key_separates_slack_from_docs_for_the_same_question():
    """Same text, different corpus → must not share a cache slot."""
    docs = _question_hash("what did we decide about pricing?")
    slack = _question_hash("what did we decide about pricing?", None, "slack")
    assert docs != slack
    # And the un-scoped key is unchanged, so existing entries stay valid.
    assert docs == _question_hash("what did we decide about pricing?", None, None)


def test_slack_chips_are_channel_shaped_not_document_shaped():
    chips = build_slack_suggestions(["eng-standup"])
    assert chips, "expected starter chips for a connected channel"
    assert all("#eng-standup" in c for c in chips)
    # Conversation framing, not "what does <title> cover" document framing.
    assert any("discussed" in c.lower() for c in chips)


def test_slack_chips_rotate_across_channels_and_dedupe():
    chips = build_slack_suggestions(["#general", "general", "eng", ""])
    assert chips
    assert any("#general" in c for c in chips)
    assert any("#eng" in c for c in chips), "second channel should appear too"
    # "#general" and "general" are the same channel; the blank is not a channel.
    assert not any("##" in c for c in chips)


def test_no_connected_channels_means_no_chips():
    assert build_slack_suggestions([]) == []


@requires_db
def test_document_chips_never_quote_a_slack_thread(store, embedder, org_cleanup):
    """A Slack "title" is message prose, not a document name.

    Poured into the document templates it produced chips like
    ``What does "No - it's intentionally limited, so it doesn't..." cover?``.
    Slack gets its own channel-shaped chips instead.

    The non-Slack row here is seeded under a synthetic provider with no
    dedicated tab of its own (not "notion"/"google" — those get excluded too
    now that they have their own Notion/Drive tabs, same reasoning as Slack;
    see ``_document_titles_for_scope``'s docstring). This test only needs to
    prove Slack rows are excluded from whatever's left in that legacy bucket.
    """
    from app.api.chat import _document_titles_for_scope

    org_id = store.create_organization(f"Chip Scope {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)

    _seed(store, embedder, org_id, "other", "Leave Policy", "Annual leave is 20 days.")
    _seed(
        store,
        embedder,
        org_id,
        "slack",
        "No - it's intentionally limited, so it doesn't pull the entire",
        "No - it's intentionally limited, so it doesn't pull the entire history.",
    )

    titles = _document_titles_for_scope(org_id, None)

    assert "Leave Policy" in titles
    assert not any(t.startswith("No - it's") for t in titles)
