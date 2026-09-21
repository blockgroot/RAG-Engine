"""The refusal a member gets when documents were WITHHELD, not missing.

Document-level access filtering removes documents the asker cannot open
*before* ranking, so a question those documents would have answered fails the
confidence gate and comes back as the ordinary "I don't know". That is a wrong
answer in the only way this codebase minds: it is indistinguishable from
"nobody has written that down", and it sends someone off to write a document
that already exists one permission away.

What may be said, and what may not, is the whole design of this module:

* NAMING THE SOURCE AND ITS SCOPE IS SAFE. ``GET /workspaces/{id}/connections``
  returns ``source_config`` to every MEMBER of a space (``get_workspace_role``,
  not owner-only), so the connected Drive folder's name is already on the space
  page in front of them. Repeating it discloses nothing.
* NAMING THE DOCUMENT IS NOT. The title of a file they have not been given
  access to is exactly the thing the filter exists to withhold — "you can't see
  *Q3 Redundancies*" tells them what is in it. So nothing here ever touches the
  matched document: ``VectorStore.restricted_match`` deliberately returns only
  a score and a provider.
* SAYING IT WHEN IT IS NOT TRUE IS WORSE THAN SILENCE. This message is built
  only after a second query has CONFIRMED that a document in scope matched the
  question and is unreadable by this person. A question nobody's documents
  answer must keep the ordinary refusal, or we send people to ask an admin for
  access to something that does not exist.
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

# Where each provider keeps the human name of the thing an admin connected.
# Only providers that are actually scope-configured appear; anything else
# falls back to the provider's own name, which is still true.
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
) -> str:
    """Tell the asker their answer exists but is not shared with them.

    Names the connector and (when we have it) the connected folder — both
    already visible to them — plus who can grant access. Never the document.
    """
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
