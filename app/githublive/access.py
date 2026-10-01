"""Per-asker repository access for live GitHub reads (CLAUDE.md §3, GitHub per-repo access).

The installation token reads every repository the App was granted, so without
this anyone in the scope could ask about a private repository they cannot open
on GitHub. A PUBLIC repository costs no call: everyone can read it, the Slack
public-channel rule. A PRIVATE one is readable only when the asker's GitHub
login -- proven by "Linked accounts" (`person_identities`), never guessed from a
name or email -- has access, per GitHub's own resolution of repo, team, org and
enterprise grants (`repo_permission`).

Fails CLOSED: no linked login, a Slack channel reply (the room reads it), an
unrecorded `private` flag or a failed check all hide the repository. A failure
is never cached, so one blip does not hide a repository for the TTL -- the
`google_groups` rule.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from ..core.exceptions import SourceError
from ..db.connection import get_connection
from ..vectorstore.base import Viewer
from .base import GitHubReader
from .repos import RepoRef

logger = logging.getLogger(__name__)

#: GitHub's legacy `permission` values that can read code.
_READS = frozenset({"admin", "maintain", "write", "triage", "read"})
#: Same TTL as Google Group expansion: tighter than an hour of polling.
_TTL_SECONDS = 600.0
#: Private repositories checked per question. ponytail: beyond this they are
#: hidden (and counted), upgrade to one `GET /user/repos` with a user token.
MAX_CHECKED = 50
_WORKERS = 8

_cache: dict[tuple[str, str, str], tuple[float, bool]] = {}
_lock = threading.Lock()


def linked_login(org_id: str, email: str) -> str | None:
    """The GitHub login this member PROVED they own, or ``None``."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT pi.external_id
              FROM person_identities pi
              JOIN users u ON u.id = pi.user_id
             WHERE pi.org_id = %s::uuid AND pi.provider = 'github'
               AND u.org_id = %s::uuid AND lower(u.email) = lower(%s)
             LIMIT 1
            """,
            (org_id, org_id, email),
        ).fetchone()
    return row[0] if row else None


class RestrictedReader(GitHubReader):
    """``inner`` narrowed to the repositories one asker may read.

    Filters the CATALOG too: a private repository's name and description are
    themselves what is being withheld, so the model is never offered one.
    """

    def __init__(self, inner: GitHubReader, org_id: str, login: str | None, *, in_channel: bool):
        self._inner = inner
        self._org_id = org_id
        self.login = login
        self.in_channel = in_channel
        self.hidden = 0
        self._repos: list[RepoRef] | None = None

    def _check(self, repo: RepoRef) -> bool:
        key = (self._org_id, repo.full_name.lower(), (self.login or "").lower())
        now = time.monotonic()
        with _lock:
            hit = _cache.get(key)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]
        try:
            allowed = self._inner.repo_permission(repo.full_name, self.login) in _READS
        except Exception:  # noqa: BLE001 - unknown is hidden, and not remembered
            logger.warning("github access: could not check %s for %s", repo.full_name, self.login)
            return False
        with _lock:
            _cache[key] = (now, allowed)
        return allowed

    def list_repos(self) -> list[RepoRef]:
        if self._repos is None:
            every = self._inner.list_repos()
            public = [r for r in every if r.private is False]
            private = [r for r in every if r.private is not False]
            visible: list[RepoRef] = list(public)
            if self.login and private:
                checked = private[:MAX_CHECKED]
                with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
                    verdicts = list(pool.map(self._check, checked))
                visible += [r for r, ok in zip(checked, verdicts) if ok]
            keep = {r.full_name for r in visible}
            self._repos = [r for r in every if r.full_name in keep]
            self.hidden = len(every) - len(self._repos)
        return self._repos

    def _allow(self, repo: str) -> None:
        wanted = (repo or "").strip().lower()
        for known in self.list_repos():
            name = known.full_name.lower()
            if wanted in (name, name.split("/", 1)[-1]):
                return
        raise SourceError(f"Repository {repo!r} is not available to you.")

    def get_readme(self, repo, *a, **kw):
        self._allow(repo)
        return self._inner.get_readme(repo, *a, **kw)

    def get_commit(self, repo, *a, **kw):
        self._allow(repo)
        return self._inner.get_commit(repo, *a, **kw)

    def list_commits(self, repo, *a, **kw):
        self._allow(repo)
        return self._inner.list_commits(repo, *a, **kw)

    def list_pull_requests(self, repo, *a, **kw):
        self._allow(repo)
        return self._inner.list_pull_requests(repo, *a, **kw)

    def get_pull_request(self, repo, *a, **kw):
        self._allow(repo)
        return self._inner.get_pull_request(repo, *a, **kw)

    def list_branches(self, repo, *a, **kw):
        self._allow(repo)
        return self._inner.list_branches(repo, *a, **kw)

    def list_reviews(self, repo, *a, **kw):
        self._allow(repo)
        return self._inner.list_reviews(repo, *a, **kw)


def restrict(reader: GitHubReader, org_id: str, viewer: Viewer | None) -> GitHubReader:
    """The reader this viewer may use. Unrestricted (ingest, eval, CLI) = as is."""
    if viewer is None or viewer.is_unrestricted:
        return reader
    login = None
    if viewer.email and not viewer.public_only:
        try:
            login = linked_login(org_id, viewer.email)
        except Exception:  # noqa: BLE001 - no login = public repositories only
            logger.warning("github access: could not read linked login", exc_info=True)
    return RestrictedReader(reader, org_id, login, in_channel=viewer.public_only)


def restricted_message(reader: GitHubReader) -> str | None:
    """Why every repository was hidden, or ``None`` when something is visible."""
    if not isinstance(reader, RestrictedReader) or reader.list_repos() or not reader.hidden:
        return None
    if reader.in_channel:
        return ("The connected GitHub repositories are private, so I can't answer about "
                "them in a channel. Ask me in a direct message and I'll check what you can open.")
    if not reader.login:
        return ("The connected GitHub repositories are private. Link your GitHub account "
                "under Account → Linked accounts so I can check which ones you can open, "
                "then ask again.")
    return (f"None of the connected GitHub repositories are shared with your GitHub "
            f"account (@{reader.login}). Ask the repository's admin for access, then ask again.")
