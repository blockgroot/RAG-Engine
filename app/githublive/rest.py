"""REST implementation of ``GitHubReader`` (GitHub Integration Plan Phase 5).

Plain ``httpx`` against GitHub's REST API — no ``PyGithub``, no ``githubkit``,
for the same reason ``httpx`` backs the Google adapter and ``notion-client`` was
chosen over the LlamaIndex reader: those libraries pull in dozens of transitive
dependencies to wrap calls we make in a few lines (CLAUDE.md §1/§2). Zero new
dependencies.

Every public method follows the same three steps, in this order, and the order is
the point:

1. ``resolve_repo`` the caller's repo string against the connection's authorized
   scope. This happens **before** any network call, because that string was
   filled in by an LLM (see ``repos.py``).
2. Issue one bounded request with retry on transient failures only.
3. Shrink the response to the configured budget, **marking** anything truncated
   so the model can tell partial evidence from complete evidence.

Error mapping worth knowing: GitHub returns **404 for a repo the token cannot
see**, exactly as Drive does, so 404 is reported as "not found or not
accessible" rather than assumed deleted — and it is never retried, because
retrying a permanent answer only burns the request budget.
"""

from __future__ import annotations

import time
from datetime import datetime

import httpx

from ..auth.github_app import GITHUB_API_BASE, github_headers
from ..config.settings import GitHubLiveSettings
from ..core.exceptions import SourceError
from .base import CommitDetail, CommitFile, CommitSummary, GitHubReader, RepoReadme
from .repos import InstallationScope, RepoRef, resolve_repo

_RAW_ACCEPT = "application/vnd.github.raw"
_JSON_ACCEPT = "application/vnd.github+json"
_TRUNCATION_MARKER = "\n\n[... truncated: content was too long to include in full ...]"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_BACKOFF_SECONDS = 8.0


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    """Cut ``text`` to ``max_bytes`` and append a visible marker if cut.

    The marker matters more than the cut: a silently-shortened README would let
    the model answer as though it had read the whole thing.
    """
    if max_bytes <= 0 or len(text.encode("utf-8", errors="ignore")) <= max_bytes:
        return text, False
    encoded = text.encode("utf-8", errors="ignore")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore") + _TRUNCATION_MARKER, True


def _is_safe_sha(sha: str) -> bool:
    """A commit-ish must be alphanumeric-ish before it goes into a URL path.

    The sha is model-supplied like the repo name, so it gets the same treatment.
    Branch names and tags are allowed (they can contain ``/``, ``.``, ``-``, ``_``)
    but path traversal is not.
    """
    candidate = (sha or "").strip()
    if not candidate or ".." in candidate:
        return False
    return all(ch.isalnum() or ch in {"-", "_", ".", "/"} for ch in candidate)


class RestGitHubReader(GitHubReader):
    """Reads one org's authorized repos over GitHub's REST API."""

    def __init__(
        self,
        token: str,
        scope: InstallationScope,
        settings: GitHubLiveSettings | None = None,
    ) -> None:
        self._token = token
        self._scope = scope
        self._settings = settings or GitHubLiveSettings.from_env()

    # -- interface ---------------------------------------------------------

    def list_repos(self) -> list[RepoRef]:
        return list(self._scope.repos)

    def get_readme(self, repo: str) -> RepoReadme:
        full_name = resolve_repo(self._scope, repo)
        response = self._request(
            f"{GITHUB_API_BASE}/repos/{full_name}/readme",
            accept=_RAW_ACCEPT,
            what=f"README of {full_name}",
        )
        content, truncated = _truncate(response.text, self._settings.readme_max_bytes)
        return RepoReadme(
            repo=full_name,
            content=content,
            url=f"https://github.com/{full_name}#readme",
            truncated=truncated,
        )

    def get_commit(self, repo: str, sha: str) -> CommitDetail:
        full_name = resolve_repo(self._scope, repo)
        if not _is_safe_sha(sha):
            raise SourceError(f"{sha!r} is not a valid commit reference.")

        payload = self._request(
            f"{GITHUB_API_BASE}/repos/{full_name}/commits/{sha.strip()}",
            accept=_JSON_ACCEPT,
            what=f"commit {sha} in {full_name}",
        ).json()

        commit = payload.get("commit") or {}
        author = commit.get("author") or {}
        stats = payload.get("stats") or {}
        all_files = payload.get("files") or []
        capped = all_files[: max(0, self._settings.max_files_per_commit)]

        files = tuple(
            CommitFile(
                path=item.get("filename", "?"),
                status=item.get("status", "modified"),
                additions=int(item.get("additions") or 0),
                deletions=int(item.get("deletions") or 0),
                patch=(
                    _truncate(item["patch"], self._settings.patch_max_bytes)[0]
                    if item.get("patch")
                    else None
                ),
            )
            for item in capped
        )

        return CommitDetail(
            repo=full_name,
            sha=str(payload.get("sha") or sha),
            message=commit.get("message", ""),
            author=author.get("name"),
            date=_parse_dt(author.get("date")),
            url=payload.get("html_url") or f"https://github.com/{full_name}/commit/{sha}",
            files=files,
            additions=int(stats.get("additions") or 0),
            deletions=int(stats.get("deletions") or 0),
            files_truncated=len(all_files) > len(capped),
            total_files=len(all_files),
        )

    def list_commits(
        self,
        repo: str,
        *,
        path: str | None = None,
        since: str | None = None,
        limit: int = 10,
    ) -> list[CommitSummary]:
        full_name = resolve_repo(self._scope, repo)
        # Clamp rather than trust: ``limit`` is model-supplied, and an absurd
        # value would paginate GitHub hard and overflow the prompt.
        per_page = max(1, min(int(limit or 10), self._settings.max_commits))

        params: dict[str, object] = {"per_page": per_page}
        if path:
            params["path"] = path
        if since:
            params["since"] = since

        payload = self._request(
            f"{GITHUB_API_BASE}/repos/{full_name}/commits",
            accept=_JSON_ACCEPT,
            params=params,
            what=f"commit history of {full_name}",
        ).json()

        summaries: list[CommitSummary] = []
        for item in payload or []:
            commit = item.get("commit") or {}
            author = commit.get("author") or {}
            # Subject line only — commit bodies are often long and add little
            # when the model is scanning history.
            subject = (commit.get("message") or "").split("\n", 1)[0].strip()
            sha = str(item.get("sha") or "")
            summaries.append(
                CommitSummary(
                    repo=full_name,
                    sha=sha,
                    message=subject,
                    author=author.get("name"),
                    date=_parse_dt(author.get("date")),
                    url=item.get("html_url")
                    or f"https://github.com/{full_name}/commit/{sha}",
                )
            )
        return summaries

    # -- HTTP --------------------------------------------------------------

    def _request(
        self,
        url: str,
        *,
        accept: str,
        what: str,
        params: dict | None = None,
    ) -> httpx.Response:
        """One bounded GET, retrying only transient failures.

        Backoff is truncated-exponential and honours ``Retry-After`` when GitHub
        supplies it (it does on 429), because guessing a shorter delay than the
        server asked for is how a client earns a longer ban.
        """
        attempts = max(1, self._settings.max_attempts)
        last_detail = ""

        for attempt in range(1, attempts + 1):
            try:
                response = httpx.get(
                    url,
                    headers=github_headers(self._token, accept=accept),
                    params=params,
                    timeout=self._settings.timeout,
                )
            except httpx.HTTPError as exc:
                if attempt >= attempts:
                    raise SourceError(
                        f"GitHub request for {what} failed: {exc}", cause=exc
                    ) from exc
                self._sleep_for(attempt, None)
                continue

            status = response.status_code
            if status < 400:
                return response

            if status in _RETRYABLE_STATUS and attempt < attempts:
                self._sleep_for(attempt, response.headers.get("Retry-After"))
                last_detail = f"HTTP {status}"
                continue

            raise SourceError(self._describe_error(status, what, response))

        raise SourceError(
            f"GitHub request for {what} failed after {attempts} attempts ({last_detail})."
        )

    @staticmethod
    def _describe_error(status: int, what: str, response: httpx.Response) -> str:
        """Turn a status code into something an admin could act on."""
        if status == 404:
            # GitHub deliberately 404s resources a token can't see, so this is
            # genuinely ambiguous and must not be reported as "deleted".
            return (
                f"Could not read {what}: not found, or not visible to this GitHub "
                "installation. Check the repository is still included in the "
                "installation on GitHub."
            )
        if status == 401:
            return (
                f"Could not read {what}: GitHub rejected the installation token. "
                "Reconnect GitHub."
            )
        if status == 403:
            return (
                f"Could not read {what}: forbidden. The installation may lack the "
                "required permission, or a rate limit is in force."
            )
        if status == 409:
            # Documented response for an empty repository.
            return f"Could not read {what}: the repository appears to be empty."
        return f"Could not read {what}: GitHub returned HTTP {status}."

    @staticmethod
    def _sleep_for(attempt: int, retry_after: str | None) -> None:
        delay = min(2.0 ** (attempt - 1), _MAX_BACKOFF_SECONDS)
        if retry_after:
            try:
                delay = min(max(float(retry_after), 0.0), _MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
        time.sleep(delay)
