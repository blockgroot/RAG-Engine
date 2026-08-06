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
    assert qs
    assert all("payments" not in q.lower() for q in qs)
    assert any("RAG" in q for q in qs)
    assert any("Fact-Verification-Engine" in q for q in qs)
    assert len(qs) <= 4


def test_github_suggestions_empty_without_repos():
    assert build_github_suggestions([]) == []
    assert build_github_suggestions([{"full_name": ""}]) == []


def test_policy_suggestions_use_document_titles():
    qs = build_policy_suggestions(["Leave Policy", "Remote Work Guide", "Benefits"])
    assert len(qs) == 3
    assert any("Leave Policy" in q for q in qs)
    assert any("Remote Work Guide" in q for q in qs)
    assert not any("maternity" in q.lower() for q in qs)


def test_policy_suggestions_empty_without_titles():
    assert build_policy_suggestions([]) == []
    assert build_policy_suggestions(["  ", ""]) == []
