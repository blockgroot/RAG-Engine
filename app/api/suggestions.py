"""Build Ask suggestion chips from connected sources — never hardcoded product copy.

Policy suggestions come from ingested document titles (org-wide or workspace).
GitHub suggestions come from the installation's stored repo list in
``oauth_connections.source_config``. Both stay askable shapes the matching
agent can actually ground (README / commits for code; retrieval for policy).
"""

from __future__ import annotations

from typing import Any


_MAX = 4


def _repo_short_name(full_name: str) -> str:
    name = (full_name or "").strip()
    if not name:
        return ""
    return name.rsplit("/", 1)[-1]


def build_github_suggestions(repos: list[dict[str, Any]]) -> list[str]:
    """Turn authorized repo rows into short Code-tab starter questions.

    ``repos`` is the JSON list stored on the GitHub connection (each item at
    least ``full_name``). Empty input → empty output (UI hides the chips).
    """
    cleaned: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in repos:
        if not isinstance(item, dict):
            continue
        full = str(item.get("full_name") or "").strip()
        short = _repo_short_name(full)
        if not short:
            continue
        key = short.lower()
        if key in seen:
            continue
        seen.add(key)
        desc = (item.get("description") or "").strip() if isinstance(item.get("description"), str) else ""
        cleaned.append((short, desc))

    if not cleaned:
        return []

    # Prefer repos that have a description for "what does it do" — they usually
    # answer cleanly from catalog metadata even when the README is a stub.
    ranked = sorted(cleaned, key=lambda pair: (0 if pair[1] else 1, pair[0].lower()))
    questions: list[str] = []

    # Up to two overview questions.
    for short, _desc in ranked[:2]:
        questions.append(f"What does the {short} repository do?")
        if len(questions) >= _MAX:
            return questions

    # Recent activity on another repo when available, else the first.
    activity_repo = ranked[2][0] if len(ranked) > 2 else ranked[0][0]
    questions.append(f"What changed recently in {activity_repo}?")
    if len(questions) >= _MAX:
        return questions

    # Setup / README-shaped ask on the first repo.
    questions.append(f"How do I run {ranked[0][0]} locally?")
    return questions[:_MAX]


def build_policy_suggestions(titles: list[str]) -> list[str]:
    """Turn ingested document titles into short Policies-tab starter questions."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in titles:
        title = " ".join((raw or "").split()).strip()
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(title)

    if not cleaned:
        return []

    questions: list[str] = []
    templates = (
        'What does "{title}" cover?',
        'What are the key rules in "{title}"?',
        'Summarize "{title}" for an employee.',
        'What should I know from "{title}"?',
    )
    for i, title in enumerate(cleaned[:_MAX]):
        # Keep chip text readable — long Notion titles get a soft trim.
        display = title if len(title) <= 64 else title[:61].rstrip() + "…"
        questions.append(templates[i % len(templates)].format(title=display))
    return questions
