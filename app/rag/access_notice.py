"""The refusal a member gets when documents were WITHHELD, not missing.

Access filtering runs before ranking, so a withheld answer looks like "I
don't know" and sends people to write a document that already exists.

* Naming the source and its connected folder is safe: every space member
  already sees them on the space page.
* Naming the DOCUMENT is not: its title is what the filter withholds, so
  ``VectorStore.restricted_match`` returns only a score and a provider.
* Said only after a second query CONFIRMED a readable-to-others match;
  otherwise the ordinary refusal stands.
"""

from __future__ import annotations

import logging

from ..db.connection import get_connection

logger = logging.getLogger(__name__)

# The asker's own words for these, not ours.
_PROVIDER_LABELS: dict[str, str] = {
    "google": "Google Drive",
    "slack": "Slack",
    "notion": "Notion",
    "linear": "Linear",
    "github": "GitHub",
}

# Where each provider keeps the name of what an admin connected; others fall
# back to the provider's own name.
_SCOPE_NAME_KEYS: dict[str, str] = {"google": "folder_name"}


def _connected_scope_name(provider: str, org_id: str, workspace_id: str | None) -> str | None:
    """The connected folder/space name for ``provider``, as the space page shows it."""
    key = _SCOPE_NAME_KEYS.get(provider)
    if not key:
        return None
    try:
        with get_connection() as conn:
            row = conn.execute(
                """
                SELECT source_config
                FROM oauth_connections
                WHERE org_id = %s::uuid
                  AND provider = %s
                  AND workspace_id IS NOT DISTINCT FROM %s::uuid
                LIMIT 1
                """,
                (org_id, provider, workspace_id),
            ).fetchone()
    except Exception:  # noqa: BLE001 - a nicer refusal must never cost the refusal
        logger.exception("Could not resolve connected scope name for %s", provider)
        return None
    config = (row[0] if row else None) or {}
    name = str(config.get(key) or "").strip()
    return name or None


def restricted_notice(
    provider: str | None,
    *,
    org_id: str,
    workspace_id: str | None,
    in_channel: bool = False,
) -> str:
    """Tell the asker their answer exists but is not shared with them.

    Names the connector and connected folder (already visible) and who can
    grant access, never the document. ``in_channel``: the reply speaks for the
    ROOM, so "shared with you" and "ask for access" do not apply.
    """
    if in_channel:
        return (
            "I found Slack messages that look like they answer this, but they're "
            "not visible to everyone in this channel, so I can't quote them here. "
            "Ask me in a direct message and I'll answer from what you can see."
        )
    source = _PROVIDER_LABELS.get(provider or "", "connected")
    scope_name = _connected_scope_name(provider or "", org_id, workspace_id)
    # A space has an owner who can share; company-wide content is an admin's.
    who = "the owner of this space" if workspace_id else "an admin"

    where = f"the {source} folder “{scope_name}”" if scope_name else f"{source}"
    return (
        f"I found documents in {where} that look like they answer this, but they "
        f"haven't been shared with you, so I can't read them on your behalf. "
        f"Ask {who} to give you access in {source}, then ask me again."
    )


def live_withheld_notice(provider: str | None) -> str:
    """The item behind this answer is gone or no longer readable (a live read
    said so). "Not found or not accessible", never "deleted": a 404 means
    either. Names the connector, never the item."""
    source = _PROVIDER_LABELS.get(provider or "", "connected")
    return (
        f"The {source} item that matched this question is no longer available — "
        f"it was deleted, or access to it was removed — so I can't answer from it. "
        f"If you still need it, check {source} directly or ask whoever owns it."
    )
