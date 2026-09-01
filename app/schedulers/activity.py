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


# How many changed documents one report may read from the index. Bounded like
# every other external walk in this codebase, and the content is bounded in SQL
# too (``left(...)``) rather than after the fetch: a monthly Notion window can
# be dozens of long pages, and pulling megabytes into Python to then throw most
# of it away is the wrong place to discover a size limit.
MAX_INDEXED_DOCS = 60
MAX_INDEXED_DOC_CHARS = 3_000

#: What one document IS, per provider — used in coverage notes so a reader
#: knows what was counted. "12 threads" and "12 files" are different claims.
_INDEXED_UNIT = {
    "slack": ("thread", "threads"),
    "notion": ("page", "pages"),
    "linear": ("issue", "issues"),
    "google": ("file", "files"),
}


def _connection_sync_state(
    org_id: str, provider: str, workspace_id: str | None
) -> datetime | None:
    """Assert the connection exists IN THIS SCOPE and return its last sync time.

    Two jobs in one query, both load-bearing:

    * **Existence.** Reading the index directly would otherwise make "this
      space has no Slack connection" indistinguishable from "nothing happened
      this week" — a broken scheduler would report quiet periods forever. The
      raise keeps a misconfiguration loud, and preserves the rule that a
      space-scoped report must never fall back to the org connection.
    * **Freshness.** An indexed report is only as current as the last sync, so
      the coverage note has to state when that was. Anything else implies live
      coverage it does not have.
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
) -> ActivityDigest:
    """Documents that CHANGED since ``since``, read from our own index.

    Why the index rather than the service's API
    ------------------------------------------
    Notion and Drive have no "what happened between T1 and T2" primitive at
    all — their adapters answer "does this exist / is it stale", so a live
    report on either could only ever list filenames. But we already store the
    full text of every changed document, and ``documents.source_last_modified``
    already records when the source last changed it. One indexed query answers
    both "which changed" and "what they say", with no API call.

    That makes all four non-GitHub sources ONE implementation instead of four,
    and it is only honest because syncing is now automatic
    (``app/jobs/autosync.py``): before that, ``source_last_modified`` only
    advanced when a human pressed Update, so an unattended report would have
    summarised whatever the last person happened to sync.

    What this can and cannot say
    ----------------------------
    It reports the CURRENT CONTENT of documents that changed in the window —
    never a diff. "The pricing page covers refunds" is supportable; "a refund
    clause was added" is not, because nothing here stores the previous version.
    The prompt profile enforces that; this function only supplies the facts.

    Two disclosures the coverage notes always carry: how many documents were
    read, and when the source was last synced. The second is the one that
    matters — a reader must not mistake an indexed report for a live one.
    """
    if provider == "slack":
        # Titles are "#channel: snippet", so a rename would otherwise show the
        # old channel name in this report's snapshotted items forever. The
        # refresh also relabels the stored titles (app/sources/slack_utils.py).
        from ..sources.slack_utils import refresh_channel_names

        refresh_channel_names(org_id, workspace_id)

    last_sync = _connection_sync_state(org_id, provider, workspace_id)

    from ..db.connection import get_connection

    with get_connection() as conn:
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
            GROUP BY d.id, d.title, d.source_uri, d.source_last_modified
            ORDER BY d.source_last_modified DESC
            LIMIT {MAX_INDEXED_DOCS + 1}
            """,
            (org_id, org_id, provider, workspace_id, since),
        ).fetchall()

    # One extra row was requested purely to detect the cap without a second
    # COUNT query — the same trick the source adapters use for paging.
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
        # Ordered newest-first, so the cap drops the oldest end of the window —
        # the same truncation shape as every live fetcher here.
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


def _indexed_fetcher(provider: str):
    """One fetcher per indexed provider, all the same function.

    Slack, Notion, Linear and Drive used to be four live-API implementations
    (and Notion/Drive could not be implemented at all — see
    ``fetch_indexed_activity``). Reading our own index makes them one, so the
    per-provider surface that remains is exactly the part that genuinely
    differs: what a "document" is called in a coverage note.

    Built as a closure rather than four wrapper functions because there is
    nothing left for a wrapper to do, and four identical bodies is four places
    for them to drift.
    """

    def fetch(
        org_id: str,
        since: datetime,
        *,
        workspace_id: str | None = None,
    ) -> ActivityDigest:
        return fetch_indexed_activity(
            org_id, since, provider=provider, workspace_id=workspace_id
        )

    fetch.__name__ = f"fetch_{provider}_activity"
    fetch.__doc__ = (
        f"What changed in {provider} since ``since``, read from the index. "
        "See ``fetch_indexed_activity`` for why this is not a live read."
    )
    return fetch


# GitHub stays LIVE and is the deliberate exception: it embeds nothing
# (``app/githublive/``), so there is no index to read, and it already has a
# real ``list_commits(since=)`` primitive. Its report is therefore the only one
# that can describe change itself ("these commits landed") rather than current
# content.
fetch_slack_activity = _indexed_fetcher("slack")
fetch_linear_activity = _indexed_fetcher("linear")
fetch_notion_activity = _indexed_fetcher("notion")
fetch_google_activity = _indexed_fetcher("google")


_FETCHERS = {
    "github": fetch_github_activity,
    "slack": fetch_slack_activity,
    "linear": fetch_linear_activity,
    "notion": fetch_notion_activity,
    # Drive's provider string is "google" — what the connect flow writes to
    # oauth_connections. Using "google_drive" here would list zero connections
    # and let no scheduler be created, silently.
    "google": fetch_google_activity,
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
