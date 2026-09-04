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


def _pull_request(full_name: str, item: dict) -> PullRequest:
    """One API object to our shape, keeping the three people distinct.

    ``state`` is OURS, not GitHub's: GitHub reports a merged pull request as
    "closed", so counting merges off its state would count every abandoned
    branch as a merge.
    """
    user = item.get("user") or {}
    merger = item.get("merged_by") or {}
    merged_at = _parse_dt(item.get("merged_at"))
    number = int(item.get("number") or 0)
    return PullRequest(
        repo=full_name,
        number=number,
        title=(item.get("title") or "").strip(),
        author=user.get("login"),
        merged_by=merger.get("login"),
        state="merged" if merged_at else str(item.get("state") or "open"),
        created_at=_parse_dt(item.get("created_at")),
        merged_at=merged_at,
        closed_at=_parse_dt(item.get("closed_at")),
        url=item.get("html_url") or f"https://github.com/{full_name}/pull/{number}",
    )


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

    def list_pull_requests(
        self,
        repo: str,
        *,
        since: datetime | None = None,
        limit: int = 100,
        state: str = "all",
    ) -> PullRequestPage:
        full_name = resolve_repo(self._scope, repo)
        cap = max(1, min(int(limit or 100), self._settings.max_pull_requests))

        # The filter goes on the REQUEST. Asking for `all` and keeping the open
        # ones out of a newest-first window of 20 returns nothing on a busy
        # repo, because the newest 20 are usually all merged -- and the caller
        # then says "no open pull requests" while several are open.
        wanted = (state or "all").lower()
        # "merged" is not a GitHub state: a merged pull request is `closed`, so
        # ask for closed and drop the ones that were abandoned rather than
        # merged. Same reason `_pull_request` derives our own `state`.
        requested = "closed" if wanted == "merged" else (
            wanted if wanted in ("open", "closed") else "all"
        )

        items: list[PullRequest] = []
        truncated = False
        page = 1
        # Sorted by `updated` descending: GitHub cannot sort on merged_at, and
        # this is the only ordering that lets a `since` window stop early. It
        # also means a cap drops the OLDEST end, never a random subset.
        while len(items) < cap:
            payload = self._request(
                f"{GITHUB_API_BASE}/repos/{full_name}/pulls",
                accept=_JSON_ACCEPT,
                params={
                    "state": requested,
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": min(100, cap - len(items)),
                    "page": page,
                },
                what=f"pull requests of {full_name}",
            ).json() or []

            if not payload:
                break

            stop = False
            for item in payload:
                updated = _parse_dt(item.get("updated_at"))
                if since and updated and updated < since:
                    # The listing is newest-first, so everything after this is
                    # older too -- stop rather than page through history.
                    stop = True
                    break
                pull = _pull_request(full_name, item)
                if wanted == "merged" and not pull.merged_at:
                    # Closed without merging. Counted against neither the cap
                    # nor the answer -- an abandoned branch is not a merge.
                    continue
                items.append(pull)

            if stop or len(payload) < 100:
                break
            page += 1

        if len(items) >= cap:
            # We filled the cap, so there may be more we did not look at. Said
            # out loud rather than inferred, because a chart built from the
            # first N while believing it complete is the failure that matters.
            truncated = True

        return PullRequestPage(items=tuple(items[:cap]), truncated=truncated)

    def get_pull_request(self, repo: str, pull_number: int) -> PullRequest | None:
        """One pull request in full, for ``merged_by``.

        GitHub omits ``merged_by`` from the pulls LIST payload and returns it
        only here, so a merger can never be read off a listing. One call per
        pull request: the caller bounds the set.
        """
        full_name = resolve_repo(self._scope, repo)
        payload = self._request(
            f"{GITHUB_API_BASE}/repos/{full_name}/pulls/{int(pull_number)}",
            accept=_JSON_ACCEPT,
            what=f"pull request {full_name}#{pull_number}",
        ).json() or {}
        if not payload:
            return None
        return _pull_request(full_name, payload)

    def list_branches(self, repo: str, *, limit: int = 50) -> list[Branch]:
        full_name = resolve_repo(self._scope, repo)
        cap = max(1, min(int(limit or 50), self._settings.max_branches))
        payload = self._request(
            f"{GITHUB_API_BASE}/repos/{full_name}/branches",
            accept=_JSON_ACCEPT,
            params={"per_page": min(100, cap)},
            what=f"branches of {full_name}",
        ).json() or []

        return [
            Branch(
                repo=full_name,
                name=str(item.get("name") or ""),
                protected=bool(item.get("protected")),
                sha=((item.get("commit") or {}).get("sha")),
            )
            for item in payload[:cap]
            if item.get("name")
        ]

    def list_reviews(self, repo: str, pull_number: int) -> list[Review]:
        full_name = resolve_repo(self._scope, repo)
        payload = self._request(
            f"{GITHUB_API_BASE}/repos/{full_name}/pulls/{int(pull_number)}/reviews",
            accept=_JSON_ACCEPT,
            params={"per_page": 100},
            what=f"reviews on {full_name}#{pull_number}",
        ).json() or []

        reviews: list[Review] = []
        for item in payload:
            user = item.get("user") or {}
            reviews.append(
                Review(
                    repo=full_name,
                    pull_number=int(pull_number),
                    reviewer=user.get("login"),
                    state=str(item.get("state") or ""),
                    submitted_at=_parse_dt(item.get("submitted_at")),
                )
            )
        return reviews

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
