"""Unit tests for Ask suggestion builders (no DB / no HTTP)."""

from __future__ import annotations

from app.api.suggestions import build_github_suggestions, build_policy_suggestions


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
    qs = build_policy_suggestions(["Flayer Sync Meeting Notes"])
    assert len(qs) == 4
    assert all("Flayer Sync Meeting Notes" in q for q in qs)
    assert any("cover" in q for q in qs)
    assert any("key rules" in q for q in qs)
    assert any("Summarize" in q for q in qs)
    assert any("should I know" in q for q in qs)


def test_policy_suggestions_empty_without_titles():
    assert build_policy_suggestions([]) == []
    assert build_policy_suggestions(["  ", ""]) == []
