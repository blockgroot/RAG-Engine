"""The authorized repo scope of one GitHub App installation (Plan Phase 4).

Two responsibilities, deliberately together because they are two halves of one
idea — *what did the admin actually authorize, and does this request stay inside
it*:

- ``fetch_installation_repos`` reads ``GET /installation/repositories``, which
  reports ``repository_selection`` (``all`` or ``selected``) plus the repos
  themselves. This is recorded at connect time (decision D5b) because
  "Connect GitHub" does **not** imply every repo: the admin chooses on GitHub's
  own install screen, and our stored view should reflect that choice rather
  than assume the widest one. It mirrors a Drive connection storing its picked
  folder id.

- ``resolve_repo`` is the **allowlist**. Every live read takes a ``repo``
  argument that was filled in by an LLM, which makes it untrusted input in
  exactly the way a user-supplied ``org_id`` would be. This function is the
  live path's equivalent of ``WHERE org_id = ...``: it normalizes the name and
  refuses anything outside the installation *before* any authenticated request
  is sent. Skipping it would let a hallucinated or coaxed repo name reach
  GitHub as a real request for someone else's repository.

Each repo's ``description`` and ``topics`` are kept on purpose. Since nothing is
embedded (decision D5), they are the only signal the model has for resolving a
vague question ("which service handles payments?") to a concrete repo — they do
the job retrieval would otherwise do, at zero storage cost.
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
    """One repository the installation can see.

    ``description``/``topics`` exist to help the model choose a repo without
    retrieval (see module docstring), not for display alone.
    """

    full_name: str
    description: str | None = None
    topics: tuple[str, ...] = ()


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
    """List the repos this installation token can access, following pagination.

    Returns ``(repository_selection, repos)``. Raises ``SourceError`` on any
    failure, per the provider-error convention.
    """
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
            )
            for item in batch
            if item.get("full_name")
        )

        # Stop when the page came back short, or we've collected everything the
        # first response said existed.
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
            }
            for repo in scope.repos
        ],
    }


def scope_from_config(config: dict | None) -> InstallationScope:
    """Rebuild a scope from stored config.

    Tolerates a config written before Phase 4 (installation id only). Such a row
    gets ``repository_selection = "selected"`` with an empty repo list, so
    ``resolve_repo`` refuses everything until the scope is refreshed — failing
    closed rather than silently assuming the widest possible access.
    """
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
            )
            for item in (config.get("repos") or [])
            if item.get("full_name")
        ),
    )


def resolve_repo(scope: InstallationScope, repo: str) -> str:
    """Normalize and authorize a model-supplied repo name, or raise.

    Accepts ``owner/name`` or a bare ``name`` (qualified with the connection's
    own account). Comparison is case-insensitive, matching GitHub. Raises
    ``SourceError`` — never returns a value the caller must remember to check —
    so a missed call site fails loudly instead of leaking access.
    """
    # Whitespace only. Deliberately NOT ``.strip("/")``: silently rewriting
    # "/handbook" into a valid name would be lenient normalization of a value an
    # LLM produced, and a stray slash is a signal the model emitted something
    # malformed. Failing is more predictable than guessing what it meant.
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

    # Under "all", every repo of the connected account is in scope — including
    # ones created after connect, which is the point of choosing "all". But the
    # OWNER is still checked: "all repositories" means all of this account's,
    # never any account's.
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
