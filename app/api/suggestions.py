"""Build Ask suggestion chips from connected sources."""

from __future__ import annotations

import re
from typing import Any


_MAX = 4

# The combined empty state shows more than a single provider's chips did: with
# four sources connected, four chips cannot represent them all, and the point of
# the combined view is that a member SEES what is connected. Six fits the
# two-column bento layout without scrolling.
_MAX_COMBINED = 6

# Fixed order so chips do not reshuffle between renders. Documents first
# because they answer the questions people actually arrive with; GitHub last
# because its answers are live reads and the least likely starting point.
_COMBINED_ORDER = ("notion", "google", "policy", "slack", "linear", "github")

_GITHUB_TEMPLATES = (
    "What does the {name} repository do?",
    "What changed recently in {name}?",
    "How do I run {name} locally?",
    "Summarize the README for {name}.",
)

_SLACK_TEMPLATES = (
    "What was discussed in #{channel} recently?",
    "Catch me up on the last few days in #{channel}.",
    "What was decided in #{channel}?",
    "Any open questions or blockers in #{channel}?",
)

_LINEAR_KIND_RE = re.compile(r"^\[([^\]]+)\]\s*")
_LINEAR_KIND_ALIASES = {
    "bug": "bug",
    "incident": "incident",
    "feature": "feature",
    "feat": "feature",
    "chore": "task",
    "task": "task",
    "improvement": "improvement",
}
_LINEAR_KIND_QUESTIONS = {
    "bug": ("What's the current status?", "What's still open?"),
    "incident": ("What happened?", "What was decided?"),
    "feature": ("Is this done?", "What's left to ship?"),
    "task": ("What's the current status?", "Is this done?"),
    "improvement": ("Is this done?", "What's left?"),
}
_LINEAR_GENERIC_QUESTIONS = (
    "What's the current status?",
    "What was decided?",
    "Summarize the discussion.",
    "Is this done?",
)

_ORG_POLICY_TEMPLATES = (
    'What does "{title}" cover?',
    'What are the key rules in "{title}"?',
    'Summarize "{title}" for an employee.',
    'What should I know from "{title}"?',
)

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


def _trim_at_word(title: str, max_len: int) -> str:
    """Trim at a word boundary so chips don't end on ``POST /v1/che…``."""
    if len(title) <= max_len:
        return title
    cut = title[: max_len].rsplit(" ", 1)[0].rstrip(".,;:/-")
    if len(cut) < max_len // 2:
        cut = title[: max_len - 1].rstrip()
    return cut + "…"


def _linear_chip_topic(title: str) -> tuple[str | None, str]:
    """Strip Linear type tags / code ticks; return (kind, short topic)."""
    raw = " ".join((title or "").split()).strip()
    kind: str | None = None
    match = _LINEAR_KIND_RE.match(raw)
    if match:
        kind = _LINEAR_KIND_ALIASES.get(match.group(1).strip().lower())
        raw = raw[match.end() :]
    raw = raw.replace("`", "").replace('"', "").replace("'", "")
    raw = " ".join(raw.split()).strip(" .-")
    if kind and raw.lower().endswith(kind):
        raw = raw[: -len(kind)].rstrip(" -")
    return kind, _trim_at_word(raw, 42)


def build_github_suggestions(repos: list[dict[str, Any]]) -> list[str]:
    """Turn authorized repo rows into short Code-tab starter questions."""
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

    ranked = sorted(cleaned, key=lambda pair: (0 if pair[1] else 1, pair[0].lower()))
    names = [short for short, _ in ranked]

    questions: list[str] = []
    for i, template in enumerate(_GITHUB_TEMPLATES):
        if len(questions) >= _MAX:
            break
        name = names[i % len(names)]
        questions.append(template.format(name=name))
    return questions


def build_slack_suggestions(channel_names: list[str]) -> list[str]:
    """Turn connected Slack channel names into Slack-tab starter questions."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in channel_names:
        name = " ".join((raw or "").split()).lstrip("#").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)

    if not cleaned:
        return []

    questions: list[str] = []
    for i, template in enumerate(_SLACK_TEMPLATES):
        if len(questions) >= _MAX:
            break
        questions.append(template.format(channel=_display_title(cleaned[i % len(cleaned)])))
    return questions


def build_linear_suggestions(titles: list[str]) -> list[str]:
    """Turn ingested Linear issue titles into Linear-tab starter questions."""
    cleaned: list[tuple[str | None, str]] = []
    seen: set[str] = set()
    for raw in titles:
        kind, topic = _linear_chip_topic(raw)
        if not topic:
            continue
        key = topic.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append((kind, topic))

    if not cleaned:
        return []

    kind_counts: dict[str, int] = {}
    questions: list[str] = []
    for i in range(_MAX):
        kind, topic = cleaned[i % len(cleaned)]
        if kind and kind in _LINEAR_KIND_QUESTIONS:
            variants = _LINEAR_KIND_QUESTIONS[kind]
            n = kind_counts.get(kind, 0)
            kind_counts[kind] = n + 1
            ask = variants[n % len(variants)]
        else:
            ask = _LINEAR_GENERIC_QUESTIONS[i % len(_LINEAR_GENERIC_QUESTIONS)]
        questions.append(f"{topic} — {ask}")
    return questions


def build_policy_suggestions(
    titles: list[str], *, workspace: bool = False
) -> list[str]:
    """Turn ingested document titles into short Policies or Space Ask chips."""
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


def build_combined_suggestions(
    per_provider: dict[str, list[str]], *, workspace: bool = False
) -> list[str]:
    """Starter chips spanning EVERY connected source, one turn each.

    Ask is a single box now (``app/agent/routing.py`` picks the agent), so a
    chip set drawn from one provider would teach the wrong thing: it reads as
    "this box is for Notion" when the point is that it is for everything. It
    would also hide a source entirely from someone who has never asked about
    it — the empty state is the only place most people learn what is
    connected.

    Interleaved round-robin rather than concatenated, because concatenation
    means the last provider never appears once ``_MAX_COMBINED`` bites: a
    tenant with Notion and Slack would show four Notion chips and no Slack.
    Providers are taken in a fixed order so the chips do not reshuffle between
    renders for no reason.

    ``per_provider`` maps a provider key to that provider's already-built
    suggestions — this function does no fetching, matching how every other
    builder here takes plain data.
    """
    order = [p for p in _COMBINED_ORDER if per_provider.get(p)]
    if not order:
        return []
    if len(order) == 1:
        return per_provider[order[0]][:_MAX_COMBINED]

    out: list[str] = []
    seen: set[str] = set()
    for round_index in range(_MAX_COMBINED):
        for provider in order:
            questions = per_provider[provider]
            if round_index >= len(questions):
                continue
            question = questions[round_index]
            key = question.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(question)
            if len(out) >= _MAX_COMBINED:
                return out
    return out
