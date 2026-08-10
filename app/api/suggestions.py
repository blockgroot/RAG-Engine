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

# Org-wide Policies tab — company handbook phrasing.
_ORG_POLICY_TEMPLATES = (
    'What does "{title}" cover?',
    'What are the key rules in "{title}"?',
    'Summarize "{title}" for an employee.',
    'What should I know from "{title}"?',
)

# Space Ask — notes/docs connected to that space, not HR "employee" framing.
_WORKSPACE_DOC_TEMPLATES = (
    'What does "{title}" cover?',
    'What are the main points in "{title}"?',
    'Summarize "{title}".',
    'What should I know from "{title}"?',
)


def _repo_short_name(full_name: str) -> str:
    name = (full_name or "").strip()
    if not name:
        return ""
    return name.rsplit("/", 1)[-1]


def _display_title(title: str, *, max_len: int = 64) -> str:
    """Keep chip text readable — long Notion titles get a soft trim."""
    if len(title) <= max_len:
        return title
    return title[: max_len - 1].rstrip() + "…"


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


def build_policy_suggestions(
    titles: list[str], *, workspace: bool = False
) -> list[str]:
    """Turn ingested document titles into short Policies / Space Ask chips.

    ``workspace=True`` uses note-oriented phrasing (no "for an employee" /
    "key rules" company-handbook copy). Empty titles → empty output. With one
    document, all four templates use that title; with several, templates
    rotate across titles (same shape as GitHub).
    """
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

    templates = _WORKSPACE_DOC_TEMPLATES if workspace else _ORG_POLICY_TEMPLATES
    questions: list[str] = []
    for i, template in enumerate(templates):
        if len(questions) >= _MAX:
            break
        title = cleaned[i % len(cleaned)]
        questions.append(template.format(title=_display_title(title)))
    return questions
