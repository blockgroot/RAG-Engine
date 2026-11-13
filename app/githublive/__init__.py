"""Live GitHub reads: the entire GitHub data path. Nothing is ingested or
embedded; every answer comes from a bounded live API read.

Public API:
    from app.githublive import InstallationScope, resolve_repo, scope_from_config
"""

from .repos import (
    SELECTION_ALL,
    SELECTION_SELECTED,
    InstallationScope,
    RepoRef,
    fetch_installation_repos,
    resolve_repo,
    scope_from_config,
    scope_to_config,
)
from .base import (
    Branch,
    CommitDetail,
    CommitFile,
    CommitSummary,
    GitHubReader,
    PullRequest,
    PullRequestPage,
    RepoReadme,
    Review,
)
from .factory import build_github_reader
from .rest import RestGitHubReader
from .scope import load_scope, refresh_installation_scope

__all__ = [
    "SELECTION_ALL",
    "SELECTION_SELECTED",
    "InstallationScope",
    "RepoRef",
    "fetch_installation_repos",
    "resolve_repo",
    "scope_from_config",
    "scope_to_config",
    "load_scope",
    "refresh_installation_scope",
    "GitHubReader",
    "RepoReadme",
    "CommitDetail",
    "CommitFile",
    "Branch",
    "CommitSummary",
    "PullRequest",
    "PullRequestPage",
    "Review",
    "RestGitHubReader",
    "build_github_reader",
]
