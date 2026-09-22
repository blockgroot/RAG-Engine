"""What needs the viewer's attention, in one list.

A connection whose token expires stops syncing SILENTLY. Nothing in the
product said so unless someone happened to open Sources, so a space kept
answering from a corpus that had quietly stopped updating -- and the person
asking could not tell a stale answer from a current one. `needs_reauth` was
already stored and already rendered on the card; the gap was that nobody
goes looking at a card that has never given them a reason to.

Scoped to what the viewer can actually FIX, not to what is wrong. An org-wide
connection is an admin's to reconnect and a space's is its OWNER's, so a
member sees neither: a notification naming a problem you cannot act on is an
alarm with no off switch. That also means this endpoint leaks nothing -- every
item names a scope the caller already administers.

Deliberately NOT a stored table. Each item is derived from the connection row
that is already the source of truth, so an item disappears the moment the
thing is fixed and there is no read/unread state to keep in agreement with
reality. Notifications that outlive their cause are the reason people stop
reading them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..auth.credentials import list_connections, sanitize_reauth_reason
from ..auth.session import SessionClaims
from ..jobs.autosync import SCOPE_KEYS
from ..workspaces.store import list_my_workspaces
from .deps import get_session

router = APIRouter(prefix="/notifications", tags=["notifications"])

#: Connectors that index NOTHING until someone picks their scope, and the
#: `source_config` key that says they have. Drive without a folder and Slack
#: without channels both report "Linked" while holding zero documents, which
#: reads as a working connection (CLAUDE.md §5 Sources).
#:
#: Imported rather than restated: `autosync` needs the same mapping to decide
#: whether a reconnect can ingest immediately, and two copies of one fact drift.
_SCOPE_KEYS = SCOPE_KEYS

_PROVIDER_NAMES: dict[str, str] = {
    "google": "Google Drive",
    "slack": "Slack",
    "notion": "Notion",
    "linear": "Linear",
    "github": "GitHub",
}


def _label(provider: str) -> str:
    return _PROVIDER_NAMES.get(provider, provider.title())


def _items_in(org_id: str, workspace_id: str | None, scope: str, href: str) -> list[dict]:
    """Attention items for one scope the caller administers."""
    items: list[dict] = []
    for conn in list_connections(org_id, workspace_id):
        name = _label(conn.provider)
        if conn.needs_reauth:
            items.append(
                {
                    "kind": "reauth",
                    "severity": "high",
                    "provider": conn.provider,
                    "scope": scope,
                    "title": f"{name} needs reconnecting",
                    # The reason is the provider's own, so it is shown rather
                    # than summarised -- "expired" and "access revoked" send
                    # someone to two different places.
                    "detail": (
                        sanitize_reauth_reason(conn.provider, conn.reauth_reason)
                        if conn.reauth_reason
                        else f"Its access expired, so {scope} has stopped syncing {name}."
                    ),
                    "action": "Reconnect",
                    "href": href,
                }
            )
            continue
        key = _SCOPE_KEYS.get(conn.provider)
        if key and not (conn.source_config or {}).get(key):
            items.append(
                {
                    "kind": "scope",
                    "severity": "medium",
                    "provider": conn.provider,
                    "scope": scope,
                    "title": f"{name} is connected but indexing nothing",
                    "detail": (
                        "Pick a folder so its documents can answer questions."
                        if conn.provider == "google"
                        else "Pick the channels Handbook may read."
                    ),
                    "action": "Choose what to read",
                    "href": href,
                }
            )
    return items


@router.get("")
def list_notifications(session: SessionClaims = Depends(get_session)):
    """Everything wrong that this caller can fix, highest severity first."""
    items: list[dict] = []
    if session.role == "admin":
        items += _items_in(session.org_id, None, "Company", "/admin/connections")
    for space in list_my_workspaces(session.org_id, session.user_id):
        # A member cannot reconnect a space's sources (the space page disables
        # every control for them), so telling them one is broken is noise.
        if space.role != "owner":
            continue
        items += _items_in(
            session.org_id, space.id, space.name, f"/workspaces/{space.id}"
        )
    items.sort(key=lambda i: 0 if i["severity"] == "high" else 1)
    return {"items": items, "count": len(items)}
