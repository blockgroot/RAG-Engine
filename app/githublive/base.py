"""The live-GitHub-read contract (GitHub Integration Plan Phase 5).

A ``GitHubReader`` answers three questions about one org's authorized repos —
what does this repo say about itself, what did this commit do, and what changed
recently — always **live**, never from a stored copy.

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
