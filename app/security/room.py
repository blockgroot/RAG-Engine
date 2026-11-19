"""Who reads a reply posted in a Slack channel: everyone in it (its ROOM).

A channel reply may use what EVERY member can open: company-public documents,
that channel's own messages, entries all members share (a domain, a group) and
each private document every member's ACL opens (`visibility.room_documents`).
One member who cannot be identified makes the room public-plus-channel only:
the reply is never wider than its least-entitled reader.
"""

from __future__ import annotations

import threading
import time

from ..vectorstore.base import Viewer

#: Seconds a room is reused: a busy channel costs one member read per window.
#: A share revoked meanwhile takes effect within it, as GitHub access does.
_TTL_SECONDS = 600.0

UNKNOWN_ROOM = (
    "I couldn't check who is in this channel, so I used only what everyone in the "
    "company can open and this channel's own messages."
)
CUT_ROOM = (
    "Some private documents everyone here can open were left out (too many to check). "
    "Ask me in a direct message for the rest."
)

_cache: dict[tuple[str, str], tuple[float, Viewer]] = {}
_lock = threading.Lock()


def room_viewer(org_id: str, channel_id: str, emails: list[str] | None) -> Viewer:
    """The viewer for a reply in ``channel_id``, whose human members are ``emails``
    (``None`` = could not be told)."""
    base = Viewer(public_only=True, channels=(channel_id,))
    if emails is None:
        return Viewer(public_only=True, channels=(channel_id,), room_note=UNKNOWN_ROOM)
    if not emails:
        return base

    from ..sources.google_groups import viewer_for_person
    from .visibility import room_documents

    acls = [viewer_for_person(org_id, email).acl() for email in emails]
    shared = sorted(set(acls[0]).intersection(*acls[1:]))
    docs, complete = room_documents(org_id, acls, shared)
    return Viewer(
        public_only=True,
        channels=(channel_id,),
        room=tuple(shared + docs),
        room_members=tuple(emails),
        room_note=None if complete else CUT_ROOM,
    )


def known_room(org_id: str, channel_id: str) -> Viewer:
    """The room as last read (within ``_TTL_SECONDS``), for a caller with no Slack
    token; else public-plus-channel. Never wider than a fresh read."""
    with _lock:
        hit = _cache.get((org_id, channel_id))
    if hit and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[1]
    return Viewer(public_only=True, channels=(channel_id,))


def cached_room_viewer(org_id: str, channel_id: str, read_emails) -> Viewer:
    """``room_viewer`` reused for ``_TTL_SECONDS``; ``read_emails()`` runs only on
    a miss. A failure is not remembered."""
    key = (org_id, channel_id)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < _TTL_SECONDS:
        return hit[1]
    viewer = room_viewer(org_id, channel_id, read_emails())
    if viewer.room_note != UNKNOWN_ROOM:
        with _lock:
            _cache[key] = (now, viewer)
    return viewer
