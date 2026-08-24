"""Fetch "what happened on this service since T", as plain text for a prompt.

This is the one part of the scheduler that differs per service, and it is
deliberately *live-read only* — the GitHub pattern (``app/githublive/``),
not the ingestion pattern. Nothing here writes a ``documents`` row, a
``chunks`` row, or an embedding: a report is composed from activity fetched
at run time and then discarded, so there is no sync lifecycle and no
staleness window to manage.

Each fetcher returns a plain-text digest rather than structured objects,
because its only consumer is an LLM prompt. Keeping the formatting here (not
in the runner) mirrors how ``SourceAdapter`` implementations own their own
format conversion — the caller never learns what a commit or a Slack message
looks like.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ..core.exceptions import ConfigurationError, SourceError

logger = logging.getLogger(__name__)

# Per-repo commit cap for one report. Bounded for the same reason every other
# fetch in this codebase is: a monorepo with hundreds of weekly commits would
# otherwise build an unbounded prompt.
MAX_COMMITS_PER_REPO = 30
MAX_REPOS = 20
MAX_SLACK_MESSAGES = 300
MAX_LINEAR_ISSUES = 300

# Total characters any one digest may reach, and the per-entry cap that keeps
# one giant item from consuming the whole budget on its own.
#
# Counting entries is NOT enough, which a real fetch proved: three actual
# Slack messages produced 6,637 characters, so 300 of them would be ~600KB of
# prompt. Same lesson as CHUNK_MAX_CHARS in app/ingestion/chunking.py — bound
# the thing itself rather than a proxy for it, because the proxy (message
# count, commit count) has no fixed relationship to size. A long-form Slack
# post and a squashed commit body are both routinely thousands of characters.
#
# ~40k leaves a large real report intact while keeping the prompt well inside
# any provider's context and its per-request cost predictable.
MAX_DIGEST_CHARS = 40_000
MAX_ENTRY_CHARS = 2_000
_TRUNCATION_MARKER = "[... truncated: more activity than fits in one report ...]"


def _clip(text: str, limit: int = MAX_ENTRY_CHARS) -> str:
    """Shorten one entry, marking it so the model can't read it as complete."""
    text = " ".join(text.split())  # collapse newlines: one entry, one line
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " […]"


def _join_bounded(lines: list[str]) -> str:
    """Join digest lines, stopping at MAX_DIGEST_CHARS with a marker.

    Truncation is *marked* rather than silent for the same reason the GitHub
    diff cap and the Notion fetch bound mark theirs: a report composed from
    half the evidence while appearing complete is the failure that matters.
    """
    out: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > MAX_DIGEST_CHARS:
            out.append(_TRUNCATION_MARKER)
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out).strip()


def fetch_github_activity(
    org_id: str,
    since: datetime,
    *,
    workspace_id: str | None = None,
) -> str:
    """Commits pushed across this connection's authorized repos since ``since``.

    Reuses ``RestGitHubReader.list_commits(since=)``, which already speaks
    GitHub's own ``since`` parameter — no adapter change was needed for
    GitHub, unlike every other source.

    A repo that fails individually is skipped with a warning rather than
    failing the whole report: one archived or permission-changed repo should
    not cost the user every other repo's activity.
    """
    from ..githublive import build_github_reader
    from ..githublive.scope import load_scope

    scope = load_scope(org_id, workspace_id)
    reader = build_github_reader(org_id, workspace_id)

    lines: list[str] = []
    repos = list(scope.repos)[:MAX_REPOS]
    if len(scope.repos) > MAX_REPOS:
        lines.append(
            f"[note: only the first {MAX_REPOS} of {len(scope.repos)} "
            "authorized repositories were checked]"
        )

    for repo in repos:
        try:
            commits = reader.list_commits(
                repo.full_name,
                since=since.isoformat(),
                limit=MAX_COMMITS_PER_REPO,
            )
        except SourceError as exc:
            logger.warning(
                "Scheduler: skipping repo %s (%s)", repo.full_name, exc
            )
            continue
        if not commits:
            continue
        lines.append(f"\nRepository {repo.full_name}:")
        for commit in commits:
            when = commit.date.strftime("%Y-%m-%d") if commit.date else "unknown date"
            author = commit.author or "unknown author"
            lines.append(
                _clip(f"- [{when}] {commit.sha[:7]} {commit.message} (by {author})")
            )

    return _join_bounded(lines)


def fetch_slack_activity(
    org_id: str,
    since: datetime,
    *,
    workspace_id: str | None = None,
) -> str:
    """Messages posted in this connection's configured channels since ``since``."""
    from ..auth.credentials import get_connection_config, get_live_connection_token
    from ..sources import build_source_adapter

    config = get_connection_config(org_id, "slack", workspace_id)
    if not config:
        raise ConfigurationError("Slack is not connected for this organization.")
    token = get_live_connection_token(org_id, "slack", workspace_id)
    adapter = build_source_adapter("slack", token=token, config=config)

    messages = adapter.fetch_recent_messages(
        since.timestamp(), max_messages=MAX_SLACK_MESSAGES
    )
    lines: list[str] = []
    for message in messages:
        when = message["at"].strftime("%Y-%m-%d %H:%M") if message["at"] else "unknown"
        replies = message.get("reply_count") or 0
        thread_note = f" [{replies} replies]" if replies else ""
        lines.append(
            _clip(
                f"- [{when}] #{message['channel']} {message['user']}: "
                f"{message['text']}{thread_note}"
            )
        )
    return _join_bounded(lines)


def fetch_linear_activity(
    org_id: str,
    since: datetime,
    *,
    workspace_id: str | None = None,
) -> str:
    """Issues updated in this connection's Linear workspace since ``since``.

    Unlike Slack and GitHub, Linear has **two** independent credential paths
    (an OAuth connection, or a legacy per-org ``LINEAR_TOKEN_<NAME>`` env
    key), and they are deliberately not linked by a fallback. A scheduler is
    created against an ``oauth_connections`` row, so this takes the OAuth
    path only — passing ``token=`` is also what tells the adapter to send
    ``Bearer <token>`` rather than a raw personal key, which is the one place
    the two paths cannot be symmetric.
    """
    from ..auth.credentials import get_connection_config, get_live_connection_token
    from ..sources import build_source_adapter

    token = get_live_connection_token(org_id, "linear", workspace_id)
    config = get_connection_config(org_id, "linear", workspace_id)
    adapter = build_source_adapter("linear", token=token, config=config)

    issues = adapter.fetch_recent_issues(since, max_issues=MAX_LINEAR_ISSUES)
    lines: list[str] = []
    for issue in issues:
        when = issue["at"].strftime("%Y-%m-%d %H:%M") if issue["at"] else "unknown"
        who = f" · {issue['assignee']}" if issue["assignee"] else " · unassigned"
        # state_type is what makes "what shipped" answerable — a state *name*
        # is workspace-specific ("Shipped", "Live", "QA"), while the type is a
        # fixed Linear vocabulary the model can reason about.
        state = issue["state"] or "unknown state"
        kind = f" ({issue['state_type']})" if issue["state_type"] else ""
        lines.append(
            _clip(
                f"- [{when}] {issue['identifier']} {issue['title']} — "
                f"{state}{kind}{who}"
            )
        )
    return _join_bounded(lines)


_FETCHERS = {
    "github": fetch_github_activity,
    "slack": fetch_slack_activity,
    "linear": fetch_linear_activity,
}


def fetch_activity(
    provider: str,
    org_id: str,
    since: datetime,
    *,
    workspace_id: str | None = None,
) -> str:
    """Activity digest for one provider since ``since``. Empty string if none.

    Raises ``ConfigurationError`` for a provider with no fetcher rather than
    returning empty: a scheduler that silently reports "no activity" every
    cycle because its source was never wired up is worse than one that fails
    visibly with ``last_error`` set.
    """
    fetcher = _FETCHERS.get(provider)
    if fetcher is None:
        raise ConfigurationError(
            f"No activity fetcher for provider {provider!r} — "
            f"supported: {sorted(_FETCHERS)}."
        )
    return fetcher(org_id, since, workspace_id=workspace_id)
