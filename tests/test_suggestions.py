"""Unit tests for Ask suggestion builders (no DB / no HTTP)."""

from __future__ import annotations

from app.api.suggestions import (
    build_github_suggestions,
    build_linear_suggestions,
    build_policy_suggestions,
)


def test_github_suggestions_use_connected_repo_names_only():
    repos = [
        {"full_name": "18sana/RAG", "description": "A RAG pipeline"},
        {"full_name": "18sana/Fact-Verification-Engine", "description": "Agents debate claims"},
        {"full_name": "18sana/SaveNServe", "description": None},
    ]
    qs = build_github_suggestions(repos)
    assert len(qs) == 4
    assert all("payments" not in q.lower() for q in qs)
    assert any("RAG" in q for q in qs)
    assert any("Fact-Verification-Engine" in q for q in qs)


def test_github_suggestions_fill_four_chips_with_one_repo():
    qs = build_github_suggestions(
        [{"full_name": "18-sana/Chain-Guard", "description": "Security checks"}]
    )
    assert len(qs) == 4
    assert all("Chain-Guard" in q for q in qs)
    assert any("repository do" in q for q in qs)
    assert any("changed recently" in q for q in qs)
    assert any("run" in q and "locally" in q for q in qs)
    assert any("README" in q for q in qs)


def test_github_suggestions_empty_without_repos():
    assert build_github_suggestions([]) == []
    assert build_github_suggestions([{"full_name": ""}]) == []


def test_policy_suggestions_use_document_titles():
    qs = build_policy_suggestions(["Leave Policy", "Remote Work Guide", "Benefits"])
    assert len(qs) == 4
    assert any("Leave Policy" in q for q in qs)
    assert any("Remote Work Guide" in q for q in qs)
    assert any("Benefits" in q for q in qs)
    assert not any("maternity" in q.lower() for q in qs)


def test_policy_suggestions_fill_four_chips_with_one_document():
    qs = build_policy_suggestions(["Leave Policy"])
    assert len(qs) == 4
    assert all("Leave Policy" in q for q in qs)
    assert any("cover" in q for q in qs)
    assert any("key rules" in q for q in qs)
    assert any("for an employee" in q for q in qs)
    assert any("should I know" in q for q in qs)


def test_linear_suggestions_use_issue_titles_not_policy_phrasing():
    qs = build_linear_suggestions(["Login button broken", "Add dark mode"])
    assert len(qs) == 4
    assert any("Login button broken" in q for q in qs)
    assert any("Add dark mode" in q for q in qs)
    assert all(" — " in q for q in qs)
    # Must not read like a handbook chip.
    assert not any("key rules" in q or "for an employee" in q for q in qs)


def test_linear_suggestions_empty_without_titles():
    assert build_linear_suggestions([]) == []


def test_linear_suggestions_are_plain_english_not_quoted_ticket_dumps():
    qs = build_linear_suggestions(
        [
            "[bug] Checkout 502 on `POST /v1/checkout`",
            "[incident] 19 Aug: search latency p99",
            '[bug] Drive sync reports "11 removed"',
            "[feature] Invoice PDF should show the wrong address",
        ]
    )
    assert len(qs) == 4
    joined = " ".join(qs)
    assert "[bug]" not in joined
    assert "[feature]" not in joined
    assert "[incident]" not in joined
    assert "`" not in joined
    assert "What is the status of" not in joined
    assert any("Checkout 502" in q and "status" in q.lower() for q in qs)
    assert any("search latency" in q.lower() and ("happened" in q.lower() or "decided" in q.lower()) for q in qs)
    assert any("Invoice PDF" in q and "done" in q.lower() for q in qs)


def test_workspace_suggestions_sound_like_notes_not_hr_policy():
    qs = build_policy_suggestions(
        ["Part-1 Foundations of Artificial Intelligence"], workspace=True
    )
    assert len(qs) == 4
    assert all("Part-1 Foundations" in q for q in qs)
    assert not any("employee" in q.lower() for q in qs)
    assert not any("key rules" in q.lower() for q in qs)
    assert any(q.startswith('Summarize "') and q.endswith('".') for q in qs)
    assert any("main points" in q for q in qs)


def test_policy_suggestions_empty_without_titles():
    assert build_policy_suggestions([]) == []
    assert build_policy_suggestions(["  ", ""]) == []


# --------------------------------------------------------------------------
# Combined chips (Ask is one box now, so chips must span every source)
# --------------------------------------------------------------------------


def test_combined_chips_represent_every_connected_source():
    """The empty state is where most people learn what is connected, so a
    provider with content must not be absent from it."""
    from app.api.suggestions import build_combined_suggestions

    out = build_combined_suggestions(
        {
            "notion": ["N1", "N2", "N3", "N4"],
            "slack": ["S1", "S2", "S3", "S4"],
            "github": ["G1", "G2"],
        }
    )

    assert {q[0] for q in out} == {"N", "S", "G"}


def test_combined_chips_interleave_rather_than_concatenate():
    """Concatenation means the last provider never appears once the cap bites:
    a Notion+Slack tenant would show four Notion chips and no Slack."""
    from app.api.suggestions import build_combined_suggestions

    out = build_combined_suggestions(
        {"notion": ["N1", "N2", "N3", "N4", "N5", "N6"], "slack": ["S1", "S2"]}
    )

    assert out[0].startswith("N")
    assert out[1].startswith("S"), "the second chip must come from the second source"


def test_combined_chips_are_bounded():
    from app.api.suggestions import build_combined_suggestions, _MAX_COMBINED

    out = build_combined_suggestions(
        {p: [f"{p}-{i}" for i in range(10)] for p in ("notion", "slack", "linear")}
    )

    assert len(out) == _MAX_COMBINED


def test_a_single_connected_source_still_fills_the_chips():
    """With one source there is nothing to interleave, and showing only one or
    two chips would look like the feature had half-loaded."""
    from app.api.suggestions import build_combined_suggestions, _MAX_COMBINED

    out = build_combined_suggestions({"notion": [f"N{i}" for i in range(8)]})

    assert len(out) == _MAX_COMBINED
    assert all(q.startswith("N") for q in out)


def test_combined_chips_never_repeat_a_question():
    """Two providers can produce the same generic question; a duplicated chip
    reads as a rendering bug."""
    from app.api.suggestions import build_combined_suggestions

    out = build_combined_suggestions(
        {"notion": ["Same question", "N2"], "google": ["Same question", "G2"]}
    )

    assert len(out) == len(set(out))


def test_no_sources_yields_no_chips():
    """Empty, not placeholder copy: a hardcoded suggestion would be the one
    thing this module exists to avoid."""
    from app.api.suggestions import build_combined_suggestions

    assert build_combined_suggestions({}) == []
    assert build_combined_suggestions({"notion": [], "slack": []}) == []
