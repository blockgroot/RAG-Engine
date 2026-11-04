"""Is there a newer Handbook than this copy, and what changed?

Every install, Cloud or self-hosted, reads one public list of releases
(``UpdateSettings.feed_url``, written by ``.github/workflows/release.yml``).
Nothing about the company is sent: it is a plain GET of a static file.

What an admin may DO with the answer depends on who runs the copy. Cloud is
one copy for every company, so we update it and admins are told what changed.
A self-hosted copy is the company's, so its admins are offered the update and
the updater container (``deploy/updater/``) carries it out.

Nothing here is stored: the bell and the page derive everything from the feed
and ``HANDBOOK_VERSION``, so a notice disappears the moment the copy runs the
new version (the same rule as ``app/api/notifications.py``).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..config.settings import UpdateSettings
from ..core.exceptions import ProviderError

logger = logging.getLogger(__name__)

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

#: The feed changes a few times a month; every admin's bell asks for it.
CACHE_SECONDS = 6 * 3600
#: After a failed fetch, try again sooner, but not on every page load.
RETRY_SECONDS = 15 * 60
#: How long admins see "Handbook was updated to X" after X is released.
WHATS_NEW_DAYS = 7
#: A feed larger than this is not ours.
MAX_RELEASES = 200


class UpdateError(ProviderError):
    """The updater could not be reached, or refused the request."""


@dataclass(frozen=True)
class Release:
    version: str
    date: str
    security: bool = False
    #: A release whose upgrade needs a person: new settings, new app scopes,
    #: a changed install package. The button is never offered across one.
    manual_steps: bool = False
    notes: list[str] = field(default_factory=list)
    notes_url: str | None = None


@dataclass(frozen=True)
class UpdateStatus:
    deployment: str
    current: str | None
    #: Releases newer than ``current``, oldest first.
    newer: list[Release]
    #: The release this copy runs, while it is recent enough to announce.
    whats_new: Release | None
    can_update: bool
    #: Why there is no button, in the admin's terms. None when there is one.
    blocked_reason: str | None

    @property
    def latest(self) -> Release | None:
        return self.newer[-1] if self.newer else None


def version_key(version: str) -> tuple[int, int, int]:
    major, minor, patch = (int(part) for part in version.split("."))
    return major, minor, patch


def parse_feed(data: object) -> list[Release]:
    """The feed's releases, newest first. Malformed entries are dropped."""
    entries = data.get("releases") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ValueError("feed has no releases list")
    releases: list[Release] = []
    for entry in entries[:MAX_RELEASES]:
        if not isinstance(entry, dict) or not VERSION_RE.match(str(entry.get("version", ""))):
            continue
        notes = entry.get("notes")
        url = entry.get("notes_url")
        releases.append(
            Release(
                version=entry["version"],
                date=str(entry.get("date", ""))[:10],
                security=entry.get("security") is True,
                manual_steps=entry.get("manual_steps") is True,
                notes=[str(n)[:300] for n in notes[:20]] if isinstance(notes, list) else [],
                # Shown as a link: only an https address, never anything else.
                notes_url=url if isinstance(url, str) and url.startswith("https://") else None,
            )
        )
    releases.sort(key=lambda r: version_key(r.version), reverse=True)
    return releases


_lock = threading.Lock()
_cache: tuple[float, str, list[Release]] | None = None


def fetch_releases(settings: UpdateSettings) -> list[Release]:
    """The feed, cached. An unreachable feed is an empty list, never an error:
    a missing notice is fine, a broken bell is not."""
    global _cache
    now = time.monotonic()
    with _lock:
        if _cache and _cache[1] == settings.feed_url and now < _cache[0]:
            return _cache[2]
    import httpx

    try:
        response = httpx.get(settings.feed_url, timeout=5.0, follow_redirects=True)
        response.raise_for_status()
        releases = parse_feed(response.json())
        expires = now + CACHE_SECONDS
    except Exception:  # noqa: BLE001 - offline, blocked or malformed: same answer
        logger.warning("Update check: could not read %s", settings.feed_url, exc_info=True)
        releases, expires = [], now + RETRY_SECONDS
    with _lock:
        _cache = (expires, settings.feed_url, releases)
    return releases


def update_status(settings: UpdateSettings | None = None) -> UpdateStatus:
    settings = settings or UpdateSettings.from_env()
    current = settings.current_version
    selfhosted = settings.deployment == "selfhosted"
    if not settings.check_enabled or not current or not VERSION_RE.match(current):
        return UpdateStatus(settings.deployment, current, [], None, False, None)

    releases = fetch_releases(settings)
    newer = sorted(
        (r for r in releases if version_key(r.version) > version_key(current)),
        key=lambda r: version_key(r.version),
    )
    running = next((r for r in releases if r.version == current), None)
    whats_new = running if running and _is_recent(running.date) else None

    blocked: str | None = None
    if not newer:
        blocked = None
    elif not selfhosted:
        blocked = "Handbook Cloud is updated by the Handbook team."
    elif any(r.manual_steps for r in newer):
        blocked = (
            "This update needs a few steps on your server first. "
            "Follow the release notes, then run the command below."
        )
    elif not settings.updater_configured:
        blocked = "The updater is not set up on this server. Run the command below instead."
    can_update = bool(newer) and blocked is None
    return UpdateStatus(settings.deployment, current, newer, whats_new, can_update, blocked)


def _is_recent(day: str) -> bool:
    try:
        released = date.fromisoformat(day)
    except ValueError:
        return False
    return date.today() - released <= timedelta(days=WHATS_NEW_DAYS)


def _updater(method: str, path: str, settings: UpdateSettings, body: dict | None = None) -> dict:
    if not settings.updater_configured:
        raise UpdateError("The updater is not set up on this server.")
    import httpx

    try:
        response = httpx.request(
            method,
            f"{settings.updater_url.rstrip('/')}{path}",
            json=body,
            headers={"Authorization": f"Bearer {settings.updater_token}"},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise UpdateError("The updater could not be reached.", cause=exc) from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        raise UpdateError(str(data.get("error") or f"The updater refused ({response.status_code})."))
    return data


def start_update(version: str, settings: UpdateSettings | None = None) -> dict:
    """Ask the updater to move this copy to ``version``. Checked here AND there:
    the updater re-reads the feed itself and trusts nothing it is sent."""
    settings = settings or UpdateSettings.from_env()
    status = update_status(settings)
    if not status.can_update:
        raise UpdateError(status.blocked_reason or "Handbook is already up to date.")
    if version not in {r.version for r in status.newer}:
        raise UpdateError(f"{version} is not a newer Handbook release.")
    return _updater("POST", "/update", settings, {"version": version})


def update_progress(settings: UpdateSettings | None = None) -> dict:
    return _updater("GET", "/status", settings or UpdateSettings.from_env())
