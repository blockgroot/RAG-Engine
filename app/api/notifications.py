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
from ..config.settings import GuardSettings
from ..feedback import assign_topics_soon, list_topics
from ..feedback.topics import WINDOW_DAYS
from ..guard.flagged import MAX_LISTED, flagged_documents
from ..auth.session import SessionClaims
from ..auth.user_connections import list_user_connections
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


def _items_in(org_id: str, workspace_id: str | None, scope: str, href: str,
              feedback_href: str) -> list[dict]:
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
                    "detail": {
                        "google": "Pick a folder so its documents can answer questions.",
                        "linear": "Pick the teams Handbook may read.",
                    }.get(conn.provider, "Pick the channels Handbook may read."),
                    "action": "Choose what to read",
                    "href": href,
                }
            )
    return items + _flagged_items(org_id, workspace_id, scope, href) + _gap_items(
        org_id, workspace_id, scope, feedback_href
    )


def _act_items(org_id: str, user_id: str) -> list[dict]:
    """The caller's OWN Act sign-ins that need reconnecting. Only they can fix
    one, so everyone sees theirs, admin or not. Never fails the bell."""
    try:
        broken = [c for c in list_user_connections(org_id, user_id) if c.needs_reauth]
    except Exception:  # noqa: BLE001 - a missing item, never a broken bell
        return []
    return [
        {
            "kind": "reauth",
            "severity": "high",
            "provider": c.provider,
            "scope": "Agent",
            "title": f"Your {_label(c.provider)} account needs reconnecting",
            "detail": f"Agent mode can't draft or send anything in {_label(c.provider)} for you until you reconnect it.",
            "action": "Reconnect",
            "href": "/account#act",
        }
        for c in broken
    ]


def _gap_items(org_id: str, workspace_id: str | None, scope: str, href: str) -> list[dict]:
    """Topics enough different people could not get answered. Never fails the bell."""
    try:
        topics = [t for t in list_topics(org_id, workspace_id) if t.alerting]
    except Exception:  # noqa: BLE001 - a missing item, never a broken bell
        return []
    items = []
    for t in topics:
        wrong = t.wrong_people > t.missing_people
        people = t.wrong_people if wrong else t.missing_people
        items.append({
            "kind": "gap",
            "severity": "medium",
            "provider": "",
            "scope": scope,
            # Counts only, never who asked.
            "title": (
                f"{people} people marked answers about \u201c{t.label}\u201d wrong"
                if wrong else
                f"{people} people couldn\u2019t get an answer about \u201c{t.label}\u201d"
            ),
            "detail": (
                f"In the last {WINDOW_DAYS} days. The document behind it may be out of date."
                if wrong else
                f"In the last {WINDOW_DAYS} days. Add a document about it, or connect where it lives."
            ),
            "action": "See the questions",
            "href": f"{href}#topic-{t.id}",
        })
    return items


def _flagged_items(org_id: str, workspace_id: str | None, scope: str, href: str) -> list[dict]:
    """Documents enforce mode is leaving out of answers. Never fails the bell."""
    guard = GuardSettings.from_env()
    if guard.mode != "enforce":
        return []
    try:
        docs = flagged_documents(org_id, workspace_id, guard.threshold)
    except Exception:  # noqa: BLE001 - a missing item, never a broken bell
        return []
    items = [
        {
            "kind": "injection",
            "severity": "medium",
            "provider": provider or "",
            "scope": scope,
            # The title may be shown: the person who can fix a document can
            # already open it in the source.
            "title": f"\u201c{title}\u201d contains text that looks like instructions to an AI",
            "detail": (
                f"Handbook leaves it out of answers until it changes. Open it in "
                f"{_label(provider or '')} and remove the instruction-like or hidden text."
            ),
            "action": "Review sources",
            "href": href,
        }
        for title, provider in docs[:MAX_LISTED]
    ]
    if len(docs) > MAX_LISTED:
        items[-1]["detail"] += " More documents are affected."
    return items


@router.get("")
def list_notifications(session: SessionClaims = Depends(get_session)):
    """Everything wrong that this caller can fix, highest severity first."""
    items: list[dict] = _act_items(session.org_id, session.user_id)
    owned =[s for s in list_my_workspaces(session.org_id, session.user_id) if s.role == "owner"]
    if session.role == "admin" or owned:
        assign_topics_soon(session.org_id)
    if session.role == "admin":
        items += _items_in(session.org_id, None, "Company", "/admin/connections", "/admin/feedback")
    # A member cannot reconnect a space's sources (the space page disables
    # every control for them), so telling them one is broken is noise.
    for space in owned:
        href = f"/workspaces/{space.id}"
        items += _items_in(session.org_id, space.id, space.name, href, f"{href}/feedback")
    items.sort(key=lambda i: 0 if i["severity"] == "high" else 1)
    return {"items": items, "count": len(items)}
