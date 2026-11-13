"""The live-GitHub-read contract: one org's authorized repos, always read live.

A ``base.py`` because a GraphQL backend is a plausible second implementation.
Return types are frozen dataclasses (the prompt's shape is ours), each with a
truncation flag: partial evidence taken as complete is the failure that matters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from .repos import RepoRef


@dataclass(frozen=True)
class RepoReadme:
    """A repo's README, fetched live and possibly truncated."""

    repo: str
    content: str
    url: str
    truncated: bool = False


@dataclass(frozen=True)
class CommitFile:
    """One file changed by a commit. ``patch`` may be absent or truncated."""

    path: str
    status: str
    additions: int = 0
    deletions: int = 0
    patch: str | None = None


@dataclass(frozen=True)
class CommitDetail:
    """One commit in enough detail to explain what it did and why."""

    repo: str
    sha: str
    message: str
    author: str | None
    date: datetime | None
    url: str
    files: tuple[CommitFile, ...] = ()
    additions: int = 0
    deletions: int = 0
    # More files changed than described: the model can say "and N more".
    files_truncated: bool = False
    total_files: int = 0


@dataclass(frozen=True)
class CommitSummary:
    """A one-line commit entry for history questions."""

    repo: str
    sha: str
    message: str  # subject line only; bodies would bloat the prompt
    author: str | None
    date: datetime | None
    url: str
    # GitHub LOGIN when matched, else None; the git display name is never an identity key.
    author_login: str | None = None


@dataclass(frozen=True)
class PullRequest:
    """One pull request, carrying the THREE distinct people: ``author``,
    ``merged_by``, and reviewers (``list_reviews``). Collapsing them hides who merges."""

    repo: str
    number: int
    title: str
    author: str | None
    merged_by: str | None
    state: str  # "open" | "closed" | "merged" -- merged is derived, not GitHub's
    created_at: datetime | None
    merged_at: datetime | None
    closed_at: datetime | None
    url: str
    #: Label names and target branch, free in the listing, chartable (activity_facts.attrs).
    labels: tuple[str, ...] = ()
    base: str | None = None
    #: Every SIMPLE field of the payload (`insights.fields.simple_fields`) as sorted
    #: pairs (hashable), so any field is chartable without a code change.
    fields: tuple[tuple[str, object], ...] = ()

    @property
    def lead_time_seconds(self) -> float | None:
        """Created to merged. ``None`` unless merged: "now" would make every stale branch slow."""
        if not (self.created_at and self.merged_at):
            return None
        return (self.merged_at - self.created_at).total_seconds()


@dataclass(frozen=True)
class Review:
    """One review event on a pull request."""

    repo: str
    pull_number: int
    reviewer: str | None
    state: str  # APPROVED | CHANGES_REQUESTED | COMMENTED | DISMISSED
    submitted_at: datetime | None


#: Review events that state a position (vs. a plain comment).
VERDICT_STATES = ("APPROVED", "CHANGES_REQUESTED")


def dedupe_reviews(reviews) -> list[Review]:
    """One review per person, keeping their actual verdict.

    Reviews come OLDEST-first: a verdict beats a comment, and between equals the
    later wins. One per person so a chatty reviewer does not outrank a thorough one.
    """
    best: dict[str, Review] = {}
    for review in reviews or ():
        who = getattr(review, "reviewer", None)
        if not who:
            continue
        current = best.get(who)
        if current is None or _review_weight(review) >= _review_weight(current):
            best[who] = review
    return list(best.values())


def _review_weight(review) -> int:
    return 2 if str(getattr(review, "state", "") or "").upper() in VERDICT_STATES else 1


@dataclass(frozen=True)
class Branch:
    """One branch. Enough to answer "what are we working on?" without a clone."""

    repo: str
    name: str
    #: GitHub-reported branch protection; never inferred.
    protected: bool = False
    #: The tip commit's own SHA, so a follow-up can fetch it.
    sha: str | None = None


@dataclass(frozen=True)
class PullRequestPage:
    """Pull requests plus whether we saw all of them. A cap drops the OLDEST end."""

    items: tuple[PullRequest, ...] = ()
    truncated: bool = False


class GitHubReader(ABC):
    """Read-only, bounded access to one org's authorized GitHub repositories.

    Implementations MUST validate every ``repo`` with ``repos.resolve_repo`` before
    any request, and raise ``SourceError`` on any failure.
    """

    @abstractmethod
    def list_repos(self) -> list[RepoRef]:
        """The repos this connection may read (name, description, topics), from stored
        scope, not a live call."""
        raise NotImplementedError

    @abstractmethod
    def get_readme(self, repo: str) -> RepoReadme:
        """Fetch ``repo``'s README as raw Markdown."""
        raise NotImplementedError

    @abstractmethod
    def get_commit(self, repo: str, sha: str) -> CommitDetail:
        """Fetch one commit: message, author, and which files it changed."""
        raise NotImplementedError

    @abstractmethod
    def list_commits(
        self,
        repo: str,
        *,
        path: str | None = None,
        since: str | None = None,
        limit: int = 10,
    ) -> list[CommitSummary]:
        """List recent commits, optionally narrowed to one file ``path``."""
        raise NotImplementedError

    @abstractmethod
    def list_pull_requests(
        self,
        repo: str,
        *,
        since: datetime | None = None,
        limit: int = 100,
        state: str = "all",
    ) -> PullRequestPage:
        """List pull requests, newest first, bounded and marking truncation.

        ``since`` filters on ``updated_at`` (the only sortable date); the caller picks
        which date a chart buckets on. ``state`` MUST go on the REQUEST, never filter
        the result (a newest-20 window has no open ones). ``"merged"`` = ``closed``
        with a ``merged_at``.
        """
        raise NotImplementedError

    @abstractmethod
    def get_pull_request(self, repo: str, pull_number: int) -> PullRequest | None:
        """One pull request in full, for ``merged_by`` (absent from the LIST payload).
        One call each: callers bound the set."""
        raise NotImplementedError

    @abstractmethod
    def list_branches(self, repo: str, *, limit: int = 50) -> list[Branch]:
        """Branches in ``repo``, bounded."""
        raise NotImplementedError

    @abstractmethod
    def list_reviews(self, repo: str, pull_number: int) -> list[Review]:
        """Reviews on one pull request: one call each (no bulk endpoint), so bound the set."""
        raise NotImplementedError
