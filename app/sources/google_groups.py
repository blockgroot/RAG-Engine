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

Nested groups: `groups.list?userKey=` returns DIRECT memberships only, so a
second pass asks `members.hasMember` (direct OR nested, any edition, same
scope) about the groups this org's documents are actually shared with. Those
are the only groups whose membership can change what anyone reads, so the
candidate set is small and known (`_nested_groups`).

Every failure here means NO groups, which is the behaviour that shipped: the
group-shared document stays withheld. That is the safe direction, and it is
why nothing in this module raises.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import httpx

from ..config.settings import GoogleSettings
from ..vectorstore.base import Viewer

logger = logging.getLogger(__name__)

_DIRECTORY_GROUPS_URL = "https://admin.googleapis.com/admin/directory/v1/groups"
_HAS_MEMBER_URL = _DIRECTORY_GROUPS_URL + "/{group}/hasMember/{member}"

#: Most `group:` grants in one org's index worth asking `hasMember` about per
#: person. The candidate set is the groups documents were ACTUALLY shared with,
#: which is small and admin-chosen; stopping here can only drop a nested
#: membership, which withholds a document rather than showing it.
MAX_NESTED_CANDIDATES = 40
#: `hasMember` calls in flight at once. This runs while a person waits for an
#: answer, so the checks go out together rather than one after another.
_NESTED_WORKERS = 8
_NESTED_TIMEOUT = 5.0

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


def _candidate_groups(org_id: str, domain: str) -> list[str] | None:
    """The groups this org's documents are shared with, in the asker's domain.

    These are the only groups whose membership can change what anyone reads,
    so they are the only ones worth asking about. Same-domain only, because
    `hasMember` resolves nesting only when the group and the member share a
    domain (anything else is an `Invalid input` error). ``None`` = could not
    read them.
    """
    from ..db.connection import get_connection

    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT substr(v, 7)
                  FROM documents d, unnest(d.doc_viewers) AS v
                 WHERE d.org_id = %s::uuid
                   AND NOT d.doc_is_public
                   AND v LIKE 'group:%%'
                   AND split_part(v, '@', 2) = %s
                 ORDER BY 1
                 LIMIT %s
                """,
                (org_id, domain, MAX_NESTED_CANDIDATES + 1),
            ).fetchall()
    except Exception:  # noqa: BLE001 - no candidates means direct groups only
        logger.debug("Could not read candidate groups for org %s", org_id, exc_info=True)
        return None
    groups = [r[0] for r in rows if r[0]]
    if len(groups) > MAX_NESTED_CANDIDATES:
        logger.warning(
            "org %s shares documents with more than %s groups in %s; nested "
            "membership is checked for the first %s only, so documents shared "
            "with the rest stay withheld from indirect members",
            org_id, MAX_NESTED_CANDIDATES, domain, MAX_NESTED_CANDIDATES,
        )
    return groups[:MAX_NESTED_CANDIDATES]


def _has_member(token: str, group: str, email: str) -> bool | None:
    """Directory `members.hasMember`: direct OR nested. ``None`` = could not tell.

    The one Directory call that answers "is this person in this group through
    any chain of groups", on every Workspace edition. `groups.list?userKey=`
    only takes a USER, so walking upward from a group is not available, and
    Cloud Identity's `searchTransitiveGroups` is Enterprise-only and needs a
    different scope (every tenant would reconnect).
    """
    url = _HAS_MEMBER_URL.format(group=quote(group, safe="@"), member=quote(email, safe="@"))
    try:
        response = httpx.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=_NESTED_TIMEOUT
        )
    except Exception:  # noqa: BLE001
        logger.debug("hasMember(%s, %s) failed", group, email, exc_info=True)
        return None
    if response.status_code in (400, 404):
        # 400 `Invalid input`: nested across domains, which Google does not
        # resolve. 404: the group no longer exists. Both are a real "no".
        return False
    if response.status_code >= 300:
        logger.debug("hasMember(%s) returned HTTP %s", group, response.status_code)
        return None
    try:
        return bool(response.json().get("isMember"))
    except Exception:  # noqa: BLE001
        return None


def _nested_groups(
    org_id: str, token: str, email: str, *, direct: tuple[str, ...]
) -> tuple[tuple[str, ...], bool]:
    """Groups ``email`` reaches only THROUGH another group, and whether we know.

    `groups.list?userKey=` returns direct memberships, so a file shared with
    `all-staff@` stayed withheld from everyone who is in it by way of `eng@`.
    Returns ``(groups, complete)``; ``complete`` is False when any check failed,
    so the caller does not cache a partial answer for the TTL.
    """
    domain = email.split("@", 1)[1] if "@" in email else ""
    if not domain:
        return (), True
    candidates = _candidate_groups(org_id, domain)
    if candidates is None:
        return (), False
    pending = [g for g in candidates if g not in set(direct)]
    if not pending:
        return (), True
    with ThreadPoolExecutor(max_workers=min(_NESTED_WORKERS, len(pending))) as pool:
        answers = list(pool.map(lambda g: _has_member(token, g, email), pending))
    found = tuple(g for g, is_member in zip(pending, answers) if is_member)
    return found, all(a is not None for a in answers)


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
    nested, complete = _nested_groups(org_id, token, address, direct=fetched)
    groups = tuple(sorted(set(fetched) | set(nested)))
    if complete:
        _store(key, groups)
    return groups


def viewer_for_person(org_id: str, email: str | None) -> Viewer:
    """The ``Viewer`` for one real, identified person -- groups and prior emails.

    The single constructor for an identified viewer, so that a surface added
    later cannot quietly ship without group expansion. ``public_only`` and
    ``unrestricted`` viewers are built from ``Viewer`` directly: neither names
    a person, so neither has memberships to honour.
    """
    address = (email or "").strip()
    if not address:
        return Viewer.public_only_viewer()
    return Viewer(
        email=address,
        groups=groups_for(org_id, address),
        aliases=_aliases_for(org_id, address),
    )


def _aliases_for(org_id: str, email: str) -> tuple[str, ...]:
    """Prior sign-in addresses; a failed read means none (fail closed)."""
    from ..auth.email_change import aliases_for

    try:
        return aliases_for(org_id, email)
    except Exception:  # noqa: BLE001 - never fail a question over an alias read
        logger.warning("Could not read prior emails for a viewer", exc_info=True)
        return ()
