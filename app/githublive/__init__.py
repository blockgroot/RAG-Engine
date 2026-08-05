"""Live GitHub reads — the entire GitHub data path (no ingestion, no embeddings).

Unlike ``app/sources/`` (Notion, Google Drive), GitHub content is never
ingested, chunked, embedded, or stored. Every answer is composed from a bounded,
live API read decided by the LLM through real function-calling, in the same
shape as the Phase 5 web-search fallback. See
``docs/plans/2026-08-05-github-integration.md`` revision 1 for why the README is
fetched live rather than indexed: it is small and rarely changes, so an index
would buy nothing while adding a sync lifecycle and a staleness window that live
fetching cannot have.

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
    CommitDetail,
    CommitFile,
    CommitSummary,
    GitHubReader,
    RepoReadme,
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
    "CommitSummary",
    "RestGitHubReader",
    "build_github_reader",
]
