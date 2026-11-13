"""Which Google Groups a person belongs to, for document access filtering.

Drive reports a group share as one opaque ``group:<address>`` grant. It is
expanded on the READ side (the asker's memberships), never at ingest: the
stored grant stays truthful, a group edit needs no re-stamp (it moves no Drive
metadata), and ``doc_viewers && acl`` is unchanged.

`groups.list?userKey=` returns direct memberships only, so nested ones are
checked with `members.hasMember` against the groups this org's documents are
actually shared with (`_nested_groups`).

Every failure means NO groups (the document stays withheld); nothing here raises.
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

#: Most `group:` grants per person to ask `hasMember` about. Stopping early only withholds.
MAX_NESTED_CANDIDATES = 40
#: `hasMember` calls in flight at once (a person is waiting).
_NESTED_WORKERS = 8
_NESTED_TIMEOUT = 5.0

#: How long a person's group list is trusted: a group removal keeps access this long
#: (shorter than a Drive revocation's sync interval).
CACHE_TTL_SECONDS = 600

#: Directory pages to walk; stopping early only drops entries (fails closed).
MAX_PAGES = 5

#: Max people cached; cleared wholesale when hit.
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

    ``None`` (failed: never cached) differs from ``()`` (in no groups). A 403 is
    expected (the account is not a Workspace admin), so it logs at debug.
    """
    # `domain` is required beside `userKey` (`customer` cannot be combined with it),
    # from the asker's address so a secondary domain resolves; external fails = no groups.
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
            # 403: not an admin or scope missing. 404: not in the directory (external): no groups.
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

    The only ones whose membership changes what anyone reads. Same domain only:
    `hasMember` does not resolve nesting across domains. ``None`` = could not read.
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

    Works on every edition with the same scope (`searchTransitiveGroups` is
    Enterprise-only and needs another scope).
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
        # 400 (cross-domain nesting) and 404 (group gone) are a real "no".
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
    """Groups ``email`` reaches only THROUGH another group: ``(groups, complete)``.
    ``complete`` is False when any check failed, so a partial answer is not cached."""
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

    Cached per ``(org_id, email)``: one tenant's directory never answers for another.
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

    # Imported here to avoid an import cycle through the OAuth provider factory.
    from ..auth.credentials import get_live_connection_token

    try:
        # The ORG-WIDE Google connection: membership belongs to the directory, not a space.
        #
        # ponytail: an org whose only Google connection is a space's gets no groups.
        # Resolve per-scope if that turns out to be common.
        token = get_live_connection_token(org_id, "google", None)
    except Exception:  # noqa: BLE001 - no connection, or a dead one, means no groups
        logger.debug("No usable Google connection for group expansion", exc_info=True)
        return ()

    fetched = _fetch_groups(token, address)
    if fetched is None:
        # A failure is not cached: a blip would lock someone out for the TTL.
        return ()
    nested, complete = _nested_groups(org_id, token, address, direct=fetched)
    groups = tuple(sorted(set(fetched) | set(nested)))
    if complete:
        _store(key, groups)
    return groups


def viewer_for_person(org_id: str, email: str | None) -> Viewer:
    """The ``Viewer`` for one identified person, with groups and prior emails.

    The single constructor for one, so no surface ships without group expansion.
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
