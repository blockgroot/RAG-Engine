"""The live-GitHub-read contract (GitHub Integration Plan Phase 5).

A ``GitHubReader`` answers questions about one org's authorized repos — what
does this repo say about itself, what did this commit do, what changed
recently, and who raised, merged and reviewed which pull requests — always
**live**, never from a stored copy.

This gets a ``base.py`` for the usual reason (CLAUDE.md §3): there is a plausible
second backend. GitHub's GraphQL API can fetch a commit and its files in a single
round trip, which is attractive given latency is on the critical path here (risk
T8), so keeping callers on an interface means that swap costs no call-site
changes. It is *not* speculative abstraction over a single implementation the way
an "RAG backend" contract would have been.

Every return type is a frozen dataclass rather than a raw API dict, for the same
reason ``SourceDocument`` exists in ``app/sources/base.py``: the shape the prompt
sees is ours, so a GitHub response-format change can't silently reshape a prompt.
Each type also carries an explicit truncation flag — a consumer must be able to
tell "this is everything" from "this is the first N", because an answer composed
from partial evidence while believing it complete is the failure mode that
matters (risk T6).
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
    # True when the commit changed more files than we're willing to describe, so
    # the model can say "and N more" instead of implying it saw everything.
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


@dataclass(frozen=True)
class PullRequest:
    """One pull request, carrying the THREE distinct people involved.

    ``author`` raised it, ``merged_by`` merged it, and reviewers come from
    ``list_reviews``. Collapsing any two of them loses the reading that
    matters: on most teams one person merges most pull requests, and that is
    invisible if "actor" silently means "author" everywhere.
    """

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

    @property
    def lead_time_seconds(self) -> float | None:
        """Created to merged. ``None`` unless it actually merged -- an open or
        abandoned pull request has no lead time, and treating "now" as its end
        would make every stale branch look like a slow merge."""
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


#: A review event that states a position on the change, as opposed to one that
#: merely leaves a comment. Used to pick which of a person's events represents
#: their review.
VERDICT_STATES = ("APPROVED", "CHANGES_REQUESTED")


def dedupe_reviews(reviews) -> list[Review]:
    """One review per person, keeping the event that is their actual verdict.

    GitHub returns reviews OLDEST-first, so keeping the first event stores the
    wrong one on the most ordinary sequence there is: comment, then approve
    would be recorded as COMMENTED, and "who approved #7?" would miss the
    person who did. A verdict therefore beats a comment, and between two
    events of equal weight the later one wins.

    Deduplicated at all because someone who comments four times on one pull
    request reviewed one pull request -- counting events would make a chatty
    reviewer outrank a thorough one.
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
    #: True when GitHub reports branch protection. Reported rather than
    #: inferred: "is main protected?" is a real question and a guess is worse
    #: than silence.
    protected: bool = False
    #: The tip commit's own SHA, so a follow-up can fetch it.
    sha: str | None = None


@dataclass(frozen=True)
class PullRequestPage:
    """Pull requests plus whether we saw all of them.

    ``truncated`` exists for the same reason every other type here carries a
    flag: a chart built from the first N while believing it complete is the
    failure that matters. A capped window only ever drops the OLDEST end,
    because the listing is sorted newest-first.
    """

    items: tuple[PullRequest, ...] = ()
    truncated: bool = False


class GitHubReader(ABC):
    """Read-only, bounded access to one org's authorized GitHub repositories.

    Implementations MUST validate every ``repo`` argument against the
    connection's authorized scope (``repos.resolve_repo``) *before* issuing any
    request, and MUST raise ``core.exceptions.SourceError`` on any failure so the
    agent can degrade to its fixed fallback rather than crash.
    """

    @abstractmethod
    def list_repos(self) -> list[RepoRef]:
        """Return the repos this connection may read (name, description, topics).

        Answered from stored scope, not a live call — the list changes only when
        an admin edits the installation. This is what lets the model choose a
        repo without any embeddings.
        """
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

        ``since`` filters on ``updated_at`` because that is the only date
        GitHub can sort on -- so a very old pull request touched yesterday is
        included, which is correct for "what moved recently" and is why the
        caller, not this method, decides which date a chart buckets on.

        ``state`` MUST be applied to the REQUEST, never by filtering the
        result. Filtering a newest-first window of 20 for ``open`` returns an
        empty list on any busy repo -- the newest 20 are usually all merged --
        and the caller then reports "no open pull requests" while several are
        open. ``"merged"`` has no GitHub equivalent: ask for ``closed`` and
        drop anything without a ``merged_at``.
        """
        raise NotImplementedError

    @abstractmethod
    def get_pull_request(self, repo: str, pull_number: int) -> PullRequest | None:
        """One pull request in full.

        Exists for exactly one field: ``merged_by`` is absent from the LIST
        payload, so the listing alone cannot answer "who merged it" and a
        chart of mergers built from it is empty. One call per pull request, so
        callers must bound the set first.
        """
        raise NotImplementedError

    @abstractmethod
    def list_branches(self, repo: str, *, limit: int = 50) -> list[Branch]:
        """Branches in ``repo``, bounded.

        A long-lived repo can have hundreds of stale branches, so this is
        capped like every other walk here -- a prompt full of dead branch names
        answers nothing.
        """
        raise NotImplementedError

    @abstractmethod
    def list_reviews(self, repo: str, pull_number: int) -> list[Review]:
        """Reviews on one pull request.

        One call PER pull request, which is why the caller must bound the pull
        request set first. There is no bulk reviews endpoint.
        """
        raise NotImplementedError
