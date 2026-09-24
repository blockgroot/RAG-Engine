"""Who is who across connectors (Second Brain step 1.2).

The same person is "Sana Asiwal" in Drive, "sana" in Slack and "18-sana" on
GitHub. ``person_identities`` holds one row per person AS EACH CONNECTOR KNOWS
THEM, and ``user_id`` says which Handbook member that is -- set only on proof
(plan decision D4):

* ``provider_email`` -- the connector reported an email equal to a member's
  login email **in the same org**. Login is by magic link, so that email is
  verified. A different org's member with the same address is never matched.
* ``oauth`` -- the member signed in to that account themselves ("Link your
  GitHub account"), because GitHub hands out logins, not emails.

Never by display name. And a link changes attribution only: nothing here is
read by retrieval's access check, which stays on ``documents``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..db.connection import get_connection

logger = logging.getLogger(__name__)

VERIFIED_BY_EMAIL = "provider_email"
VERIFIED_BY_OAUTH = "oauth"


@dataclass(frozen=True)
class Identity:
    id: str
    provider: str
    external_id: str
    email: str | None
    display_name: str | None
    user_id: str | None
    verified_by: str | None


def identity_external_id(person: dict) -> str | None:
    """The ``external_id`` column for one ``source_meta`` person entry.

    The connector's own id when it gave one, else ``email:<addr>`` -- the same
    choice ``sources.meta.person_key`` makes for the key, so the two agree.
    """
    key = person.get("key") or ""
    provider = person.get("provider") or ""
    if key.startswith(f"{provider}:"):
        return key[len(provider) + 1:]
    if key.startswith("email:"):
        return key
    return None


def upsert_identities(org_id: str, people: list[dict]) -> int:
    """Record every person a sync saw. Returns rows written.

    Never touches ``user_id``/``verified_by`` -- only linking decides those. A
    later sync that sees a better email or name for the same id updates them;
    one that sees NONE keeps what we had (``COALESCE``), because a payload that
    omits a field is not a claim that the field is empty.
    """
    rows = []
    seen: set[tuple[str, str]] = set()
    for person in people:
        provider = person.get("provider")
        external_id = identity_external_id(person)
        if not provider or not external_id or (provider, external_id) in seen:
            continue
        seen.add((provider, external_id))
        rows.append(
            (org_id, provider, external_id, person.get("email"), person.get("name"))
        )
    if not rows:
        return 0
    with get_connection() as conn:
        conn.cursor().executemany(
            """
            INSERT INTO person_identities (org_id, provider, external_id, email, display_name)
            VALUES (%s::uuid, %s, %s, lower(%s), %s)
            ON CONFLICT (org_id, provider, external_id) DO UPDATE
               SET email        = COALESCE(EXCLUDED.email, person_identities.email),
                   display_name = COALESCE(EXCLUDED.display_name, person_identities.display_name),
                   last_seen    = now()
            """,
            rows,
        )
    return len(rows)


def auto_link_by_email(org_id: str) -> int:
    """Link identities whose email is a member's login email in THIS org.

    Two statements, both scoped to one org:

    1. link: an unlinked identity whose email equals a member's email, where
       that member belongs to the same org. ``users.email`` is globally unique,
       so there is at most one candidate -- and the ``u.org_id`` check is what
       stops a member of another org who happens to share the address.
    2. unlink: an EMAIL-verified link whose email no longer matches (the
       connector now reports a different address, or the member left). An
       OAuth link is never touched here -- it was proved by the member.

    Returns identities newly linked.
    """
    with get_connection() as conn:
        linked = conn.execute(
            """
            UPDATE person_identities pi
               SET user_id = u.id, verified_by = %s
              FROM users u
             WHERE pi.org_id = %s::uuid
               AND pi.user_id IS NULL
               AND pi.email IS NOT NULL
               AND lower(u.email) = pi.email
               AND u.org_id = pi.org_id
            """,
            (VERIFIED_BY_EMAIL, org_id),
        ).rowcount
        conn.execute(
            """
            UPDATE person_identities pi
               SET user_id = NULL, verified_by = NULL
             WHERE pi.org_id = %s::uuid
               AND pi.verified_by = %s
               AND NOT EXISTS (
                   SELECT 1 FROM users u
                    WHERE u.id = pi.user_id
                      AND u.org_id = pi.org_id
                      AND pi.email IS NOT NULL
                      AND lower(u.email) = pi.email
               )
            """,
            (org_id, VERIFIED_BY_EMAIL),
        )
    return max(linked or 0, 0)


def link_github(org_id: str, user_id: str, login: str, display_name: str | None) -> Identity:
    """Attach ``github:<login>`` to the member who just proved they own it.

    The login is lowercased because GitHub logins are case-insensitive and
    ``activity_facts.actor_key`` stores them that way. If another member had
    linked the same account, the link MOVES to whoever proved it most recently:
    signing in to GitHub is the proof, and an account can change hands. It is
    logged, and it moves attribution only.
    """
    login = login.strip().lower()
    with get_connection() as conn:
        previous = conn.execute(
            """
            SELECT user_id::text FROM person_identities
             WHERE org_id = %s::uuid AND provider = 'github' AND external_id = %s
            """,
            (org_id, login),
        ).fetchone()
        row = conn.execute(
            """
            INSERT INTO person_identities
                (org_id, provider, external_id, display_name, user_id, verified_by)
            VALUES (%s::uuid, 'github', %s, %s, %s::uuid, %s)
            ON CONFLICT (org_id, provider, external_id) DO UPDATE
               SET user_id      = EXCLUDED.user_id,
                   verified_by  = EXCLUDED.verified_by,
                   display_name = COALESCE(EXCLUDED.display_name, person_identities.display_name),
                   last_seen    = now()
            RETURNING id::text, provider, external_id, email, display_name,
                      user_id::text, verified_by
            """,
            (org_id, login, display_name, user_id, VERIFIED_BY_OAUTH),
        ).fetchone()
    if previous and previous[0] and previous[0] != user_id:
        logger.info(
            "identity: github:%s moved from user %s to user %s in org %s (re-proved by OAuth)",
            login, previous[0], user_id, org_id,
        )
    return Identity(*row)


def unlink(org_id: str, user_id: str, identity_id: str) -> bool:
    """Remove a member's OWN OAuth link. Returns whether anything changed.

    Email-verified links are not unlinkable: the next sync would re-create
    them from the same proof, so offering the button would be a lie. The
    member changes that by changing the email the connector reports.
    """
    with get_connection() as conn:
        changed = conn.execute(
            """
            UPDATE person_identities
               SET user_id = NULL, verified_by = NULL
             WHERE id = %s::uuid AND org_id = %s::uuid AND user_id = %s::uuid
               AND verified_by = %s
            """,
            (identity_id, org_id, user_id, VERIFIED_BY_OAUTH),
        ).rowcount
    return bool(changed)


def list_for_user(org_id: str, user_id: str) -> list[Identity]:
    """Every identity linked to one member, for "Linked accounts"."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id::text, provider, external_id, email, display_name,
                   user_id::text, verified_by
              FROM person_identities
             WHERE org_id = %s::uuid AND user_id = %s::uuid
             ORDER BY provider, external_id
            """,
            (org_id, user_id),
        ).fetchall()
    return [Identity(*row) for row in rows]
