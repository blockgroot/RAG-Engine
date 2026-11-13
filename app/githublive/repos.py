"""The authorized repo scope of one GitHub App installation.

- ``fetch_installation_repos`` records, at connect, what the admin chose on
  GitHub's install screen (``all`` or ``selected`` repos), never assuming all.
- ``resolve_repo`` is the **allowlist**: every live read's ``repo`` is filled by
  an LLM, so it is untrusted, like a client ``org_id``. It refuses anything
  outside the installation BEFORE any authenticated request.

``description`` and ``topics`` are kept: with nothing embedded, they are how
the model picks a repo for a vague question.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..auth.github_app import GITHUB_API_BASE, github_headers
from ..core.exceptions import SourceError

_PER_PAGE = 100
_MAX_PAGES = 50  # 5000 repos; a guard against an unbounded pagination loop.
_TIMEOUT = 15.0

SELECTION_ALL = "all"
SELECTION_SELECTED = "selected"


@dataclass(frozen=True)
class RepoRef:
    """One repository the installation can see; ``description``/``topics`` help the model pick it."""

    full_name: str
    description: str | None = None
    topics: tuple[str, ...] = ()
    #: GitHub's `private` flag. ``None`` = not recorded; `githublive.access` treats it as private.
    private: bool | None = None


@dataclass(frozen=True)
class InstallationScope:
    """Everything we know about what one connection is allowed to read."""

    installation_id: str
    account_login: str
    repository_selection: str
    repos: tuple[RepoRef, ...] = ()


def fetch_installation_repos(
    token: str, *, timeout: float = _TIMEOUT
) -> tuple[str, list[RepoRef]]:
    """List the repos this installation token can access: ``(repository_selection,
    repos)``. Raises ``SourceError`` on any failure."""
    repos: list[RepoRef] = []
    selection = SELECTION_SELECTED  # conservative default; see scope_from_config
    total_count: int | None = None

    for page in range(1, _MAX_PAGES + 1):
        try:
            response = httpx.get(
                f"{GITHUB_API_BASE}/installation/repositories",
                headers=github_headers(token),
                params={"per_page": _PER_PAGE, "page": page},
                timeout=timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise SourceError(
                f"GitHub installation/repositories failed: {exc}", cause=exc
            ) from exc

        payload = response.json()
        selection = payload.get("repository_selection") or selection
        if total_count is None:
            total_count = payload.get("total_count")

        batch = payload.get("repositories") or []
        repos.extend(
            RepoRef(
                full_name=item["full_name"],
                description=item.get("description"),
                topics=tuple(item.get("topics") or ()),
                private=item.get("private"),
            )
            for item in batch
            if item.get("full_name")
        )

        # Stop on a short page or once everything announced is collected.
        if len(batch) < _PER_PAGE:
            break
        if total_count is not None and len(repos) >= total_count:
            break

    return selection, repos


def scope_to_config(scope: InstallationScope) -> dict:
    """Serialize a scope into the ``oauth_connections.source_config`` JSONB shape."""
    return {
        "installation_id": scope.installation_id,
        "account_login": scope.account_login,
        "repository_selection": scope.repository_selection,
        "repos": [
            {
                "full_name": repo.full_name,
                "description": repo.description,
                "topics": list(repo.topics),
                "private": getattr(repo, "private", None),
            }
            for repo in scope.repos
        ],
    }


def scope_from_config(config: dict | None) -> InstallationScope:
    """Rebuild a scope from stored config. An installation-id-only row gets
    ``selected`` with no repos, so ``resolve_repo`` refuses everything (fails closed)."""
    config = config or {}
    return InstallationScope(
        installation_id=str(config.get("installation_id") or ""),
        account_login=str(config.get("account_login") or ""),
        repository_selection=config.get("repository_selection") or SELECTION_SELECTED,
        repos=tuple(
            RepoRef(
                full_name=item.get("full_name", ""),
                description=item.get("description"),
                topics=tuple(item.get("topics") or ()),
                private=item.get("private"),
            )
            for item in (config.get("repos") or [])
            if item.get("full_name")
        ),
    )


def resolve_repo(scope: InstallationScope, repo: str) -> str:
    """Normalize and authorize a model-supplied repo name, or raise ``SourceError``.

    ``owner/name`` or bare ``name`` (the connection's account), case-insensitive.
    Raising (never a value to check) makes a missed call site fail loudly.
    """
    # Whitespace only: never strip "/" from model output; malformed fails.
    candidate = (repo or "").strip()
    if not candidate:
        raise SourceError("No repository was named, so nothing can be looked up.")

    if "/" in candidate:
        parts = candidate.split("/")
        if len(parts) != 2 or not all(parts):
            raise SourceError(
                f"{repo!r} is not a valid repository name (expected 'owner/name')."
            )
        owner, name = parts
    else:
        if not scope.account_login:
            raise SourceError(
                f"{repo!r} has no owner and this connection has no recorded account; "
                "reconnect GitHub."
            )
        owner, name = scope.account_login, candidate

    if not _is_plain_segment(owner) or not _is_plain_segment(name):
        raise SourceError(f"{repo!r} is not a valid repository name.")

    full_name = f"{owner}/{name}"

    # Under "all", any repo of the connected account (incl. new ones), but the
    # OWNER is still checked.
    if scope.repository_selection == SELECTION_ALL:
        if owner.lower() != scope.account_login.lower():
            raise SourceError(
                f"Repository {full_name!r} belongs to {owner!r}, which is not the "
                f"connected GitHub account ({scope.account_login!r})."
            )
        return full_name

    for known in scope.repos:
        if known.full_name.lower() == full_name.lower():
            return known.full_name

    raise SourceError(
        f"Repository {full_name!r} is not authorized for this GitHub connection. "
        "An admin must add it to the installation on GitHub to make it available."
    )


def _is_plain_segment(value: str) -> bool:
    """Reject path traversal and anything that isn't a GitHub name character."""
    if not value or value in {".", ".."}:
        return False
    return all(ch.isalnum() or ch in {"-", "_", "."} for ch in value)
