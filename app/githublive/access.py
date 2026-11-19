"""Per-asker repository access for live GitHub reads.

The installation token reads every granted repo, so a PRIVATE one is readable
only when the asker's linked GitHub login (`person_identities`, never guessed)
has access per GitHub (`repo_permission`). A PUBLIC one costs no call.

Fails CLOSED: no linked login, a Slack channel reply, an unrecorded `private`
flag or a failed check hide it. Failures are never cached.
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
#: GitHub calls a channel's ROOM may spend per question (one per member per
#: private repo, cached). ponytail: past it the rest are hidden and counted;
#: upgrade to one org-membership read if rooms grow.
ROOM_CHECK_BUDGET = 200
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
    """``inner`` narrowed to the repositories one asker may read, catalog included
    (a private repo's name and description are themselves withheld)."""

    def __init__(self, inner: GitHubReader, org_id: str, login: str | None, *, in_channel: bool,
                 room_logins: tuple[str, ...] = ()):
        self._inner = inner
        self._org_id = org_id
        self.login = login
        self.in_channel = in_channel
        #: A channel's members' logins: a private repo is visible only when EVERY
        #: one can open it. Empty in a channel = some member has no linked login.
        self.room_logins = room_logins
        self.hidden = 0
        self._repos: list[RepoRef] | None = None

    def _check(self, repo: RepoRef, login: str | None = None) -> bool:
        login = login or self.login
        key = (self._org_id, repo.full_name.lower(), (login or "").lower())
        now = time.monotonic()
        with _lock:
            hit = _cache.get(key)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]
        try:
            allowed = self._inner.repo_permission(repo.full_name, login) in _READS
        except Exception:  # noqa: BLE001 - unknown is hidden, and not remembered
            logger.warning("github access: could not check %s for %s", repo.full_name, login)
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
            if self.in_channel and self.room_logins and private:
                # Every member must open it: a repo costs one check per member.
                per = len(self.room_logins)
                checked = private[:min(MAX_CHECKED, ROOM_CHECK_BUDGET // per)]
                pairs = [(r, login) for r in checked for login in self.room_logins]
                with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
                    verdicts = list(pool.map(lambda p: self._check(*p), pairs))
                for i, repo in enumerate(checked):
                    if all(verdicts[i * per:(i + 1) * per]):
                        visible.append(repo)
            elif self.login and not self.in_channel and private:
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
    return RestrictedReader(reader, org_id, login, in_channel=viewer.public_only,
                            room_logins=_room_logins(org_id, viewer))


def _room_logins(org_id: str, viewer: Viewer) -> tuple[str, ...]:
    """Every room member's linked login, or ``()`` when any member has none (or
    it cannot be read): a private repo is then hidden from the whole room."""
    if not viewer.public_only or not viewer.room_members:
        return ()
    logins: list[str] = []
    try:
        for email in viewer.room_members:
            login = linked_login(org_id, email)
            if not login:
                return ()
            logins.append(login)
    except Exception:  # noqa: BLE001 - unknown = nothing private
        logger.warning("github access: could not read the room's linked logins", exc_info=True)
        return ()
    return tuple(logins)


#: Why a private repository is hidden, said in every refusal that hides one.
_RULE = ("I answer about a private repository only for a GitHub account you have "
         "linked that can open it.")


def _fix(login: str | None, them: str) -> str:
    """What the asker can do: one place, so the two refusals never disagree."""
    if not login:
        return (f"{_RULE} Link your GitHub account on your Account page "
                "(Your accounts), then ask again.")
    return (f"{_RULE} Your linked account, @{login}, can't open {them}. Link the "
            "account that can (Account page, \"Link another GitHub\"), or ask the "
            f"repository's admin to give @{login} access, then ask again.")


def link_would_help(reader: GitHubReader) -> bool:
    """True when linking a GitHub account could reveal a hidden repository
    (the web chat then offers a button to the Account page; never in a channel)."""
    return (isinstance(reader, RestrictedReader) and bool(reader.hidden)
            and not reader.in_channel)


def hidden_note(reader: GitHubReader) -> str | None:
    """Why a refusal may be about a repository this asker cannot see, or ``None``.

    Added to a GitHub refusal when SOME repositories were hidden: with one public
    repo still visible, ``restricted_message`` stays silent, and a question about
    a hidden private repo read as "not found". A count, never a name.
    """
    if not isinstance(reader, RestrictedReader) or not reader.hidden:
        return None
    n = reader.hidden
    what = f"{n} private repositor{'y is' if n == 1 else 'ies are'} hidden from you"
    if reader.in_channel:
        return (f"{n} private repositor{'y is' if n == 1 else 'ies are'} left out here: I use "
                "a private repository in a channel only when everyone in it has linked a "
                "GitHub account that can open it. Ask me in a direct message to use what you "
                "can open.")
    return f"{what}. " + _fix(reader.login, "it" if n == 1 else "them")


def restricted_message(reader: GitHubReader) -> str | None:
    """Why every repository was hidden, or ``None`` when something is visible."""
    if not isinstance(reader, RestrictedReader) or reader.list_repos() or not reader.hidden:
        return None
    if reader.in_channel:
        return ("The connected GitHub repositories are private, and I use a private "
                "repository in a channel only when everyone in it has linked a GitHub account "
                "that can open it. Ask me in a direct message and I'll check what you can open.")
    return "The connected GitHub repositories are private. " + _fix(reader.login, "them")


#: Seconds a viewer's visible-repo list is reused: one chart reads it several times.
_VISIBLE_TTL_SECONDS = 60.0
_visible: dict[tuple, tuple[float, list[str]]] = {}


def visible_repo_names(org_id: str, workspace_id: str | None, viewer: Viewer | None) -> list[str] | None:
    """Lowercased full names of the repos ``viewer`` may read, for filtering stored
    GitHub facts (charts); ``None`` = unrestricted, no filter. A private repo's
    activity is as private as its code. No connection or a failure: ``[]``
    (fail closed)."""
    if viewer is None or viewer.is_unrestricted:
        return None
    key = (org_id, workspace_id, viewer.email, viewer.public_only, viewer.room_members,
           viewer.channels)
    now = time.monotonic()
    with _lock:
        hit = _visible.get(key)
    if hit and now - hit[0] < _VISIBLE_TTL_SECONDS:
        return hit[1]
    try:
        from .factory import build_github_reader

        reader = restrict(build_github_reader(org_id, workspace_id), org_id, viewer)
        names = sorted({r.full_name.lower() for r in reader.list_repos()})
    except Exception:  # noqa: BLE001 - unknown = no private activity shown
        logger.info("github access: no visible repos for charts in %s", org_id)
        return []
    with _lock:
        _visible[key] = (now, names)
    return names
