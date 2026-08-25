"""Fetch "what happened on this service since T", for a report prompt.

This is the one part of the scheduler that differs per service, and it is
deliberately *live-read only* — the GitHub pattern (``app/githublive/``),
not the ingestion pattern. Nothing here writes a ``documents`` row, a
``chunks`` row, or an embedding: a report is composed from activity fetched
at run time and then discarded, so there is no sync lifecycle and no
staleness window to manage.

Each fetcher returns an ``ActivityDigest``: the flattened text an LLM prompt
consumes, PLUS the same items as structured ``ActivityItem`` records
carrying a source link, PLUS coverage notes.

**Why items are structured rather than only flattened.** The email renders
every item's link itself, from this data. The model is never asked to write
a URL, so it cannot fabricate one, drop one, or mangle one — the failure
mode with links is silent and expensive, because a plausible-looking wrong
commit URL is worse than no link at all. Keeping per-service formatting here
(not in the runner) mirrors how ``SourceAdapter`` implementations own their
own format conversion — the caller never learns what a commit or a Slack
message looks like.

**Coverage notes** exist for the same honesty reason as the truncation
markers: a Slack connection only ever sees the channels an admin picked, so
a report that said nothing about which channels it read would imply
whole-workspace coverage it never had.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
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


def _stamp(value) -> str:
    """A timestamp a person reads, not an ISO string.

    "20 Aug, 7:11 am" rather than "2026-08-20 07:11". These strings end up in
    the report page's activity list and in the LLM's context, and a wall of
    machine timestamps is the single biggest reason that list reads as
    clutter. Year is omitted deliberately: every item in a report is inside
    one weekly or monthly window, so the year is never the distinguishing
    part.
    """
    if not value:
        return "unknown date"
    # %-d/%-I are POSIX (no zero padding); the platform is macOS/Linux here.
    return value.strftime("%-d %b, %-I:%M %p").replace("AM", "am").replace("PM", "pm")


def _stamp_day(value) -> str:
    """Date only — for sources whose timestamps are day-granular in practice."""
    return value.strftime("%-d %b") if value else "unknown date"


def _clip(text: str, limit: int = MAX_ENTRY_CHARS) -> str:
    """Shorten one entry, marking it so the model can't read it as complete."""
    text = " ".join(text.split())  # collapse newlines: one entry, one line
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + " […]"


@dataclass(frozen=True)
class ActivityItem:
    """One thing that happened, with a link back to it where one exists.

    ``summary`` is the human one-liner (never contains the URL — the report
    page renders that from ``url``, so the model never handles a link).

    ``meta`` is the who/where/when prefix, kept SEPARATE from the content
    rather than concatenated into ``summary``: as one string the report page
    could only render a flat clamped line, where "Sana in #product · 20 Aug,
    5:20 am · 3 replies" competed with the message itself for the two lines
    available. Split, the page can set the meta small and give the content the
    room. The prompt still sees them joined (``_digest``), because the model
    needs both to attribute anything.
    """

    summary: str
    url: str | None = None
    meta: str | None = None


@dataclass(frozen=True)
class ActivityDigest:
    """Everything one report run knows about the window it covers."""

    items: tuple[ActivityItem, ...] = ()
    #: Coverage disclosures and truncation warnings, shown to the model AND
    #: the reader. Never silently dropped.
    notes: tuple[str, ...] = ()
    #: Flattened, bounded text for the prompt.
    text: str = ""

    def __bool__(self) -> bool:
        """Truthy only when something actually happened.

        Notes alone ("channels checked: …") are not activity — otherwise a
        quiet week would call the LLM with nothing to summarise, which is
        exactly where invention happens.
        """
        return bool(self.items)


def _digest(items: list[ActivityItem], notes: list[str]) -> ActivityDigest:
    """Bound the item list to the char budget and flatten it for the prompt.

    Truncation drops items from the *end* and records a note, so the reader
    is told the report is partial rather than being handed half the evidence
    that looks whole.
    """
    kept: list[ActivityItem] = []
    lines: list[str] = []
    used = 0
    for item in items:
        line = _clip(item.summary)
        # The model sees meta and content as one line — it needs the author and
        # timestamp to attribute anything. Only the PAGE keeps them apart.
        prompt_line = f"{item.meta} — {line}" if item.meta else line
        if used + len(prompt_line) + 1 > MAX_DIGEST_CHARS:
            notes = [*notes, _TRUNCATION_MARKER]
            break
        kept.append(ActivityItem(summary=line, url=item.url, meta=item.meta))
        lines.append(prompt_line)
        used += len(prompt_line) + 1
    return ActivityDigest(
        items=tuple(kept), notes=tuple(notes), text="\n".join(lines).strip()
    )


def fetch_github_activity(
    org_id: str,
    since: datetime,
    *,
    workspace_id: str | None = None,
) -> ActivityDigest:
    """Commits pushed across this connection's authorized repos since ``since``.

    Reuses ``RestGitHubReader.list_commits(since=)``, which already speaks
    GitHub's own ``since`` parameter — no adapter change was needed for
    GitHub, unlike every other source.

    A repo that fails individually is skipped, but the skip is *disclosed* in
    the notes rather than only logged: one archived or permission-changed
    repo should not cost the user every other repo's activity, and it must
    not silently make the report look complete either.
    """
    from ..githublive import build_github_reader
    from ..githublive.scope import load_scope

    scope = load_scope(org_id, workspace_id)
    reader = build_github_reader(org_id, workspace_id)

    repos = list(scope.repos)[:MAX_REPOS]
    notes: list[str] = []
    if len(scope.repos) > MAX_REPOS:
        notes.append(
            f"Only the first {MAX_REPOS} of {len(scope.repos)} authorized "
            "repositories were checked."
        )

    # The reader clamps ``limit`` to GITHUB_MAX_COMMITS, so the effective cap
    # can be lower than MAX_COMMITS_PER_REPO. Compute it here rather than
    # assume it, or the truncation note below never fires.
    from ..config.settings import GitHubLiveSettings

    per_repo = min(MAX_COMMITS_PER_REPO, GitHubLiveSettings.from_env().max_commits)

    items: list[ActivityItem] = []
    unreachable: list[str] = []
    capped: list[str] = []
    for repo in repos:
        try:
            commits = reader.list_commits(
                repo.full_name,
                since=since.isoformat(),
                limit=per_repo,
            )
        except SourceError as exc:
            logger.warning("Scheduler: skipping repo %s (%s)", repo.full_name, exc)
            unreachable.append(repo.full_name)
            continue
        if len(commits) >= per_repo:
            # GitHub returns commits newest-first, so the cap drops the oldest
            # end of the window — but the reader must be told it happened.
            capped.append(repo.full_name)
        for commit in commits:
            when = _stamp_day(commit.date)
            author = commit.author or "unknown author"
            items.append(
                ActivityItem(
                    summary=commit.message,
                    meta=f"{repo.full_name} · {author} · {when}",
                    # Prefer the URL GitHub itself returned; fall back to the
                    # canonical form only if the API omitted it.
                    url=commit.url
                    or f"https://github.com/{repo.full_name}/commit/{commit.sha}",
                )
            )

    checked = [r.full_name for r in repos if r.full_name not in unreachable]
    if checked:
        notes.append(f"Repositories checked: {', '.join(checked)}.")
    if unreachable:
        notes.append(
            "Could not be read this run: " + ", ".join(unreachable) + "."
        )
    if capped:
        notes.append(
            f"Only the {per_repo} most recent commits were read in "
            + ", ".join(capped)
            + " — older commits in this window were left out."
        )
    return _digest(items, notes)


def fetch_slack_activity(
    org_id: str,
    since: datetime,
    *,
    workspace_id: str | None = None,
) -> ActivityDigest:
    """Messages posted in this connection's configured channels since ``since``.

    Always discloses which channels were read. A Slack connection only ever
    sees the channels an admin picked and the bot was invited to, so a report
    that stayed silent about scope would read as whole-workspace coverage it
    never had — and the reader has no way to know otherwise.
    """
    from ..auth.credentials import get_connection_config, get_live_connection_token
    from ..sources import build_source_adapter

    # Refresh the channel labels before reading, so this report's coverage
    # note names the channel as it is called TODAY. The note is snapshotted
    # into the report, so a stale label here is wrong forever — and a run is
    # weekly or monthly, which makes one extra listing call free in practice.
    from ..sources.slack_utils import refresh_channel_names

    refresh_channel_names(org_id, workspace_id)

    config = get_connection_config(org_id, "slack", workspace_id)
    if not config:
        raise ConfigurationError("Slack is not connected for this organization.")
    token = get_live_connection_token(org_id, "slack", workspace_id)
    adapter = build_source_adapter("slack", token=token, config=config)

    messages, truncated = adapter.fetch_recent_messages(
        since.timestamp(), max_messages=MAX_SLACK_MESSAGES
    )

    channels = adapter.channel_labels()
    notes: list[str] = []
    if channels:
        notes.append(
            "Channels checked: " + ", ".join(f"#{c}" for c in channels) + "."
        )
    if truncated:
        # Hitting the message cap is disclosed, not just logged: the reader
        # would otherwise read "Channels checked: #general" as complete.
        notes.append(
            "Only the most recent messages were read in "
            + ", ".join(f"#{c}" for c in dict.fromkeys(truncated))
            + " — older activity in this window was left out."
        )

    items: list[ActivityItem] = []
    for message in messages:
        when = _stamp(message["at"])
        replies = message.get("reply_count") or 0
        thread_note = f" · {replies} replies" if replies else ""
        items.append(
            ActivityItem(
                summary=message["text"],
                meta=(
                    f"{message['user']} in #{message['channel']} · {when}{thread_note}"
                ),
                url=message.get("permalink"),
            )
        )
    return _digest(items, notes)


def fetch_linear_activity(
    org_id: str,
    since: datetime,
    *,
    workspace_id: str | None = None,
) -> ActivityDigest:
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
    items: list[ActivityItem] = []
    for issue in issues:
        when = _stamp(issue["at"])
        who = f" · {issue['assignee']}" if issue["assignee"] else " · unassigned"
        # state_type is what makes "what shipped" answerable — a state *name*
        # is workspace-specific ("Shipped", "Live", "QA"), while the type is a
        # fixed Linear vocabulary the model can reason about.
        state = issue["state"] or "unknown state"
        kind = f" ({issue['state_type']})" if issue["state_type"] else ""
        items.append(
            ActivityItem(
                summary=f"{issue['identifier']}: {issue['title']}",
                meta=f"{state}{kind}{who} · {when}",
                url=issue.get("url") or None,
            )
        )
    # Linear grants no per-team subset here, so scope is "whatever this token
    # can see" — stating that is more honest than listing nothing.
    notes = ["Scope: all Linear issues visible to this connection."]
    if len(issues) >= MAX_LINEAR_ISSUES:
        # Ordered by updatedAt descending, so the cap drops the least recently
        # updated issues — disclosed rather than silently dropped.
        notes.append(
            f"Only the {MAX_LINEAR_ISSUES} most recently updated issues were "
            "read — older updates in this window were left out."
        )
    return _digest(items, notes)


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
) -> ActivityDigest:
    """Activity digest for one provider since ``since``.

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
