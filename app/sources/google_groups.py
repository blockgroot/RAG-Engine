"""Which Google Groups a person belongs to, for document-level access filtering.

Drive reports a group share as ONE opaque ``group:<address>`` grant and never
expands it. Before this module that grant matched nobody -- fail-closed and
correct, but it meant a file shared only with ``engineering@corp.com`` was
unreadable by the forty people actually in engineering, which reads as the
feature being broken rather than as a permission being honoured.

Expansion happens on the READ side (the asker's own memberships), never the
write side (the members of each granted group), and that choice is the design:

* **The stored grant stays truthful.** ``doc_viewers`` keeps
  ``group:engineering@corp.com``, so it is still possible to tell "shared with
  the eng group" from "shared with these forty people" -- and a new joiner is
  covered without touching a single document row.
* **One call per PERSON, not per group.** A corpus has far more distinct
  groups than it has people asking questions in a cache window.
* **Membership changes need no re-stamp and no re-embed.** Expanding at ingest
  would reintroduce exactly the problem ``_restamp_unchanged_access`` exists to
  solve, except worse: a group edit moves no Drive metadata at all, so nothing
  would even signal that a re-stamp was due.
* **The SQL does not change.** ``doc_viewers && acl`` already does the work;
  this only adds entries to the asker's side of the overlap, the same way
  ``domain:<host>`` already does.

Every failure here means NO groups, which is the behaviour that shipped: the
group-shared document stays withheld. That is the safe direction, and it is
why nothing in this module raises.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from ..config.settings import GoogleSettings
from ..vectorstore.base import Viewer

logger = logging.getLogger(__name__)

_DIRECTORY_GROUPS_URL = "https://admin.googleapis.com/admin/directory/v1/groups"

#: How long a person's group list is trusted. A removal from a group keeps
#: access for at most this long -- tighter than the hour a Drive permission
#: revocation already costs (auto-sync's interval), so this is not the weakest
#: link. Short enough to matter, long enough that a chat session does not spend
#: a directory call per question.
CACHE_TTL_SECONDS = 600

#: Directory pages to walk. Groups come back 200 at a time and nobody is in
#: 1,000 groups; stopping early can only ever DROP entries, which locks someone
#: out rather than letting them in (CLAUDE.md §2: bound every external walk).
MAX_PAGES = 5

#: Ceiling on distinct people held in the cache, so a large tenant cannot grow
#: it without bound. Cleared wholesale when hit -- an LRU here would be a data
#: structure in service of a dictionary that is normally a few hundred entries.
MAX_CACHE_ENTRIES = 2000

_cache: dict[tuple[str, str], tuple[float, tuple[str, ...]]] = {}
_cache_lock = threading.Lock()


def _cached(key: tuple[str, str]) -> tuple[str, ...] | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expires_at, groups = entry
        if expires_at < time.time():
            _cache.pop(key, None)
            return None
        return groups


def _store(key: tuple[str, str], groups: tuple[str, ...]) -> None:
    with _cache_lock:
        if len(_cache) >= MAX_CACHE_ENTRIES:
            _cache.clear()
        _cache[key] = (time.time() + CACHE_TTL_SECONDS, groups)


def clear_cache() -> None:
    """Drop every cached membership. For tests and for a manual reconnect."""
    with _cache_lock:
        _cache.clear()


def _fetch_groups(token: str, email: str, *, timeout: float = 10.0) -> tuple[str, ...] | None:
    """Ask the Directory API which groups ``email`` is in. ``None`` = could not.

    ``None`` and ``()`` are deliberately different: the first means we failed to
    find out (do not cache a failure as an answer), the second means the
    directory answered and this person is in no groups.

    A 403 is the EXPECTED failure, not an exceptional one -- the Directory API
    requires the connected account to be a Workspace admin, and most connected
    accounts are not. It is logged at debug for that reason; logging it as an
    error would fill a tenant's logs with a configuration fact.
    """
    # `domain` is REQUIRED alongside `userKey`: the reference says one of
    # `domain`/`customer` must be present, and `customer` may not be combined
    # with `userKey` at all. Taken from the asker's own address rather than the
    # connection's, so a secondary domain in the same Workspace resolves to
    # itself. An address outside the Workspace simply fails, which is the
    # correct "no groups" for an external collaborator.
    domain = email.split("@", 1)[1] if "@" in email else ""
    if not domain:
        return ()

    groups: list[str] = []
    page_token: str | None = None
    for _ in range(MAX_PAGES):
        params = {"userKey": email, "domain": domain, "maxResults": 200}
        if page_token:
            params["pageToken"] = page_token
        try:
            response = httpx.get(
                _DIRECTORY_GROUPS_URL,
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=timeout,
            )
        except Exception:  # noqa: BLE001 - a directory outage must not cost the answer
            logger.debug("Directory group lookup failed for %s", email, exc_info=True)
            return None
        if response.status_code in (403, 404):
            # 403: the connected account is not a Workspace admin, or the scope
            # was never granted. 404: this address is not in the directory at
            # all (an external collaborator, or a personal Gmail account), which
            # is a true "no groups" rather than a failure.
            logger.debug(
                "Directory group lookup for %s returned HTTP %s; treating as no groups",
                email, response.status_code,
            )
            return () if response.status_code == 404 else None
        if response.status_code >= 400:
            logger.warning(
                "Directory group lookup returned HTTP %s", response.status_code
            )
            return None
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            logger.debug("Directory group lookup returned unparseable JSON", exc_info=True)
            return None
        for group in payload.get("groups") or []:
            address = str(group.get("email") or "").strip().lower()
            if address:
                groups.append(address)
        page_token = payload.get("nextPageToken")
        if not page_token:
            return tuple(sorted(set(groups)))
    logger.warning(
        "Directory group lookup for %s hit the %s-page bound; some memberships "
        "are missing and documents shared with those groups stay withheld",
        email, MAX_PAGES,
    )
    return tuple(sorted(set(groups)))


def groups_for(org_id: str, email: str) -> tuple[str, ...]:
    """Group addresses ``email`` belongs to, or ``()`` when unknown or disabled.

    Cached per ``(org_id, email)``: the org scopes the cache because the token
    that answered belongs to that org's connection, and one tenant's directory
    must never answer for another's.
    """
    address = (email or "").strip().lower()
    if not address:
        return ()
    try:
        if not GoogleSettings.from_env().groups_enabled:
            return ()
    except Exception:  # noqa: BLE001 - unreadable config is not a reason to fail a question
        logger.debug("Could not read Google settings for group expansion", exc_info=True)
        return ()

    key = (org_id, address)
    hit = _cached(key)
    if hit is not None:
        return hit

    # Imported here rather than at module scope: `credentials` reaches the OAuth
    # provider factory, and `sources` is imported by the ingestion path that
    # factory can itself reach.
    from ..auth.credentials import get_live_connection_token

    try:
        # The ORG-WIDE Google connection, deliberately: group membership is a
        # property of the Workspace directory, not of whichever space indexed a
        # file, so a space's own connection would answer the same question with
        # a token more likely to lack admin rights.
        #
        # ponytail: an org whose ONLY Google connection lives on a space gets no
        # groups. Resolve per-scope if that turns out to be common.
        token = get_live_connection_token(org_id, "google", None)
    except Exception:  # noqa: BLE001 - no connection, or a dead one, means no groups
        logger.debug("No usable Google connection for group expansion", exc_info=True)
        return ()

    fetched = _fetch_groups(token, address)
    if fetched is None:
        # A failure is NOT cached as an answer -- caching "no groups" for ten
        # minutes because the directory blipped would lock someone out of every
        # group-shared document for that window.
        return ()
    _store(key, fetched)
    return fetched


def viewer_for_person(org_id: str, email: str | None) -> Viewer:
    """The ``Viewer`` for one real, identified person -- groups included.

    The single constructor for an identified viewer, so that a surface added
    later cannot quietly ship without group expansion. ``public_only`` and
    ``unrestricted`` viewers are built from ``Viewer`` directly: neither names
    a person, so neither has memberships to honour.
    """
    address = (email or "").strip()
    if not address:
        return Viewer.public_only_viewer()
    return Viewer(email=address, groups=groups_for(org_id, address))
