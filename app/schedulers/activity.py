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
                f"- [{when}] {commit.sha[:7]} {commit.message} (by {author})"
            )

    return "\n".join(lines).strip()


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
            f"- [{when}] #{message['channel']} {message['user']}: "
            f"{message['text']}{thread_note}"
        )
    return "\n".join(lines).strip()


_FETCHERS = {
    "github": fetch_github_activity,
    "slack": fetch_slack_activity,
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
