"""Build Ask suggestion chips from connected sources — never hardcoded product copy.

Policy suggestions come from ingested document titles (org-wide or workspace).
GitHub suggestions come from the installation's stored repo list in
``oauth_connections.source_config``. Both stay askable shapes the matching
agent can actually ground (README / commits for code; retrieval for policy).
"""

from __future__ import annotations

from typing import Any


_MAX = 4

# Code starters the GitHub agent can actually answer (README / commits / about).
# Always fill up to ``_MAX`` chips even with a single connected repo — the empty
# Code page should feel complete, not half-empty.
_GITHUB_TEMPLATES = (
    "What does the {name} repository do?",
    "What changed recently in {name}?",
    "How do I run {name} locally?",
    "Summarize the README for {name}.",
)


def _repo_short_name(full_name: str) -> str:
    name = (full_name or "").strip()
    if not name:
        return ""
    return name.rsplit("/", 1)[-1]


def build_github_suggestions(repos: list[dict[str, Any]]) -> list[str]:
    """Turn authorized repo rows into short Code-tab starter questions.

    ``repos`` is the JSON list stored on the GitHub connection (each item at
    least ``full_name``). Empty input → empty output (UI hides the chips).

    With one repo, all four templates use that name. With several, templates
    rotate across repos so chips stay varied without inventing fake repos.
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
        desc = (
            (item.get("description") or "").strip()
            if isinstance(item.get("description"), str)
            else ""
        )
        cleaned.append((short, desc))

    if not cleaned:
        return []

    # Prefer repos that have a description for early "what does it do" chips —
    # they usually answer cleanly from catalog metadata even when the README is
    # a stub.
    ranked = sorted(cleaned, key=lambda pair: (0 if pair[1] else 1, pair[0].lower()))
    names = [short for short, _ in ranked]

    questions: list[str] = []
    for i, template in enumerate(_GITHUB_TEMPLATES):
        if len(questions) >= _MAX:
            break
        name = names[i % len(names)]
        questions.append(template.format(name=name))
    return questions


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
