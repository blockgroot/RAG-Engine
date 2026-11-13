"""Fetch "what happened on this service since T", for a report prompt.

Read-only: GitHub live, the other sources from our own index. Nothing is
written. Each fetcher returns an ``ActivityDigest``: prompt text, the same
items with their links as data (the model never writes a URL, so it cannot
invent one), and coverage notes, so a report never implies coverage it lacks.
"""

from __future__ import annotations

from ..vectorstore.base import Viewer

import logging
from dataclasses import dataclass, field
from datetime import datetime

from ..core.exceptions import ConfigurationError, SourceError

logger = logging.getLogger(__name__)

# Per-repo commit cap: a busy monorepo would otherwise build an unbounded prompt.
MAX_COMMITS_PER_REPO = 30
MAX_REPOS = 20
MAX_SLACK_MESSAGES = 300
MAX_LINEAR_ISSUES = 300

# Bound characters, not item counts: three real Slack messages were 6.6k
# chars. The per-entry cap stops one giant item eating the budget.
MAX_DIGEST_CHARS = 40_000
MAX_ENTRY_CHARS = 2_000
_TRUNCATION_MARKER = "[... truncated: more activity than fits in one report ...]"


def _stamp(value) -> str:
    """A readable timestamp ("20 Aug, 7:11 am"); no year, since a report
    window is never longer than a month or so.
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
    """One thing that happened. ``summary`` never holds the URL (the page
    renders ``url``); ``meta`` (who/where/when) is kept apart so the page can
    style it, and is joined back for the prompt in ``_digest``.
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
        """Truthy only on items: notes alone must not call the LLM on nothing."""
        return bool(self.items)


def _digest(items: list[ActivityItem], notes: list[str]) -> ActivityDigest:
    """Bound items to the char budget and flatten them; truncation drops from
    the end and adds a note, so a partial report never looks whole.
    """
    kept: list[ActivityItem] = []
    lines: list[str] = []
    used = 0
    for item in items:
        line = _clip(item.summary)
        # The model needs author and time to attribute anything.
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


# Documents per report; content is also cut in SQL (``left(...)``), not after
# pulling megabytes into Python.
MAX_INDEXED_DOCS = 60
MAX_INDEXED_DOC_CHARS = 3_000

#: What one document is per provider, for coverage notes.
_INDEXED_UNIT = {
    "slack": ("thread", "threads"),
    "notion": ("page", "pages"),
    "linear": ("issue", "issues"),
    "google": ("file", "files"),
}


def _connection_sync_state(
    org_id: str, provider: str, workspace_id: str | None
) -> datetime | None:
    """Assert the connection exists in this scope; return its last sync.

    Raises when missing, or a broken scheduler would report quiet weeks
    forever (and a space must never fall back to the org connection). The sync
    time goes in the notes: an indexed report is not a live one.
    """
    from ..db.connection import get_connection

    with get_connection() as conn:
        row = conn.execute(
            "SELECT last_sync_at FROM oauth_connections "
            "WHERE org_id = %s AND provider = %s "
            "AND workspace_id IS NOT DISTINCT FROM %s",
            (org_id, provider, workspace_id),
        ).fetchone()
    if row is None:
        scope = "this space" if workspace_id else "this organization"
        raise ConfigurationError(
            f"{provider} is not connected for {scope}."
        )
    return row[0]


def fetch_indexed_activity(
    org_id: str,
    since: datetime,
    *,
    provider: str,
    workspace_id: str | None = None,
    viewer: "Viewer | None" = None,
) -> ActivityDigest:
    """Documents that changed since ``since``, read from our own index.

    ``viewer`` applies document access as retrieval does: a scheduler is
    personal, and without it a digest would mail files they cannot open.
    The index, not the APIs: Notion and Drive have no "changed between" call,
    and autosync keeps ``source_last_modified`` current. Reports current
    content, never a diff; the notes say how many were read and when synced.
    """
    if provider == "slack":
        # Refresh "#channel: snippet" titles so a renamed channel shows its
        # current name.
        from ..sources.slack_utils import refresh_channel_names

        refresh_channel_names(org_id, workspace_id)

    last_sync = _connection_sync_state(org_id, provider, workspace_id)

    from ..db.connection import get_connection

    from ..security.visibility import viewer_clause

    viewer_sql, viewer_params = viewer_clause(viewer, alias="d")

    with get_connection(org_id=org_id) as conn:
        rows = conn.execute(
            f"""
            SELECT d.title,
                   d.source_uri,
                   d.source_last_modified,
                   left(string_agg(c.content, E'\n\n' ORDER BY c.chunk_index),
                        {MAX_INDEXED_DOC_CHARS})
            FROM documents d
            JOIN chunks c ON c.document_id = d.id
            WHERE d.org_id = %s
              AND c.org_id = %s
              AND d.source_provider = %s
              AND d.workspace_id IS NOT DISTINCT FROM %s
              AND d.source_last_modified > %s
              {viewer_sql}
            GROUP BY d.id, d.title, d.source_uri, d.source_last_modified
            ORDER BY d.source_last_modified DESC
            LIMIT {MAX_INDEXED_DOCS + 1}
            """,
            (org_id, org_id, provider, workspace_id, since, *viewer_params),
        ).fetchall()

    # One extra row detects the cap without a COUNT.
    capped = len(rows) > MAX_INDEXED_DOCS
    rows = rows[:MAX_INDEXED_DOCS]

    items = [
        ActivityItem(
            summary=(content or "").strip(),
            meta=f"{(title or 'Untitled').strip()} · {_stamp(changed_at)}",
            url=source_uri,
        )
        for title, source_uri, changed_at, content in rows
    ]

    singular, plural = _INDEXED_UNIT.get(provider, ("document", "documents"))
    notes: list[str] = []
    if items:
        unit = singular if len(items) == 1 else plural
        notes.append(f"{len(items)} {unit} changed in this period.")
    if capped:
        # Newest-first, so the cap drops the oldest.
        notes.append(
            f"Only the {MAX_INDEXED_DOCS} most recently changed {plural} were "
            "read — older changes in this window were left out."
        )
    notes.append(
        "Read from indexed content, last synced "
        + (_stamp(last_sync) if last_sync else "never")
        + " — not a live read, and it describes what these "
        + f"{plural} say now rather than what changed inside them."
    )
    return _digest(items, notes)


def fetch_github_activity(
    org_id: str,
    since: datetime,
    *,
    workspace_id: str | None = None,
    viewer: "Viewer | None" = None,
) -> ActivityDigest:
    """Commits pushed across this connection's authorized repos since ``since``.

    A failing repo is skipped and named in the notes, so the rest still
    report and the result never looks complete when it is not.
    """
    from ..githublive import build_github_reader
    from ..githublive.access import restrict
    from ..githublive.scope import load_scope

    scope = load_scope(org_id, workspace_id)
    full = build_github_reader(org_id, workspace_id)
    # Only repos the owner can open on GitHub (`githublive.access`).
    reader = restrict(full, org_id, viewer)
    visible = list(scope.repos) if reader is full else reader.list_repos()

    repos = list(visible)[:MAX_REPOS]
    notes: list[str] = []
    hidden = getattr(reader, "hidden", 0)
    if hidden:
        notes.append(
            f"{hidden} private repositories were left out: your linked GitHub account "
            "cannot open them, or no GitHub account is linked (Account → Linked accounts)."
        )
    if len(visible) > MAX_REPOS:
        notes.append(
            f"Only the first {MAX_REPOS} of {len(visible)} authorized "
            "repositories were checked."
        )

    # The reader clamps to GITHUB_MAX_COMMITS; use the real cap or the
    # truncation note never fires.
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
            # Newest-first: the cap drops the oldest, and the notes say so.
            capped.append(repo.full_name)
        for commit in commits:
            when = _stamp_day(commit.date)
            author = commit.author or "unknown author"
            items.append(
                ActivityItem(
                    summary=commit.message,
                    meta=f"{repo.full_name} · {author} · {when}",
                    # The URL GitHub returned, else the canonical form.
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


def _indexed_fetcher(provider: str):
    """One fetcher per indexed provider, all ``fetch_indexed_activity``."""

    def fetch(
        org_id: str,
        since: datetime,
        *,
        workspace_id: str | None = None,
        viewer: "Viewer | None" = None,
    ) -> ActivityDigest:
        return fetch_indexed_activity(
            org_id, since, provider=provider, workspace_id=workspace_id, viewer=viewer
        )

    fetch.__name__ = f"fetch_{provider}_activity"
    fetch.__doc__ = (
        f"What changed in {provider} since ``since``, read from the index. "
        "See ``fetch_indexed_activity`` for why this is not a live read."
    )
    return fetch


# GitHub stays live: it embeds nothing, and has ``list_commits(since=)``.
fetch_slack_activity = _indexed_fetcher("slack")
fetch_linear_activity = _indexed_fetcher("linear")
fetch_notion_activity = _indexed_fetcher("notion")
fetch_google_activity = _indexed_fetcher("google")


_FETCHERS = {
    "github": fetch_github_activity,
    "slack": fetch_slack_activity,
    "linear": fetch_linear_activity,
    "notion": fetch_notion_activity,
    # Drive's provider string is "google", as in oauth_connections.
    "google": fetch_google_activity,
}


def fetch_activity(
    provider: str,
    org_id: str,
    since: datetime,
    *,
    workspace_id: str | None = None,
    viewer: "Viewer | None" = None,
) -> ActivityDigest:
    """Activity digest for one provider since ``since``.

    Raises ``ConfigurationError`` for an unknown provider rather than
    reporting "no activity" forever.
    """
    fetcher = _FETCHERS.get(provider)
    if fetcher is None:
        raise ConfigurationError(
            f"No activity fetcher for provider {provider!r} — "
            f"supported: {sorted(_FETCHERS)}."
        )
    # GitHub uses ``viewer`` to keep to the repos the asker can open.
    return fetcher(org_id, since, workspace_id=workspace_id, viewer=viewer)
