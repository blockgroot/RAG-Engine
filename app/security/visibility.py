"""The ONE spelling of "may this viewer read this document?".

``doc_is_public OR doc_viewers && acl`` (CLAUDE.md §3). Every reader
(retrieval, chips, digests, the graph walk) splices this fragment so they
cannot disagree.

* The ACL array is a BOUND parameter, never formatted in (entries are emails).
* ``doc_viewers && '{}'`` is FALSE, so an empty ACL means scope-public only,
  which is what makes ``Viewer.public_only_viewer()`` safe.
* A ``doc:<id>`` entry admits exactly that document (a channel's room).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the vector store imports this module; avoid a cycle
    from ..vectorstore.base import Viewer

#: ``doc:<id>`` is matched as if it were in that document's own viewers: a Slack
#: channel's room carries one per private document EVERY member can open
#: (`security.room`), which no single overlap of entries can say. ONE placeholder.
_PREDICATE = (
    "({prefix}doc_is_public OR "
    "array_append({prefix}doc_viewers, 'doc:' || {prefix}id::text) && {param}::text[])"
)


def visibility_predicate(alias: str | None = "d", *, param: str | None = None) -> str:
    """The bare predicate, parenthesised, with ONE placeholder for the ACL array.

    ``alias`` is the `documents` alias (``None`` = unqualified); ``param``
    names a NAMED placeholder (default positional ``%s``). No leading ``AND``,
    so `restricted_match` can negate it.
    """
    prefix = f"{alias}." if alias else ""
    placeholder = f"%({param})s" if param else "%s"
    return _PREDICATE.format(prefix=prefix, param=placeholder)


_EVIDENCE_PREDICATE = "({prefix}is_public IS TRUE OR {prefix}viewers && %s::text[])"


def evidence_predicate(alias: str = "ev") -> str:
    """The rule for graph evidence that is NOT a document (a GitHub fact, a
    ``same_person`` link), which carries its own ``is_public``/``viewers``.
    ``is_public IS TRUE``, so NULL fails closed. ONE ``%s`` for the ACL."""
    return _EVIDENCE_PREDICATE.format(prefix=f"{alias}.")


def viewer_clause(viewer: "Viewer | None", alias: str | None = "d") -> tuple[str, list[list[str]]]:
    """``(" AND <predicate>", [acl])`` for ``viewer``, or ``("", [])`` when
    unrestricted (no clause at all, not one matching everything)."""
    if viewer is None or viewer.is_unrestricted:
        return "", []
    return " AND " + visibility_predicate(alias), [viewer.acl()]


def normalize_viewers(viewers: list[str] | None) -> list[str] | None:
    """Lowercase, de-duplicate and drop blanks: the write side of
    ``Viewer.acl``. Both sides must agree, or a real grant stops matching."""
    if not viewers:
        return None
    seen = {entry.strip().lower() for entry in viewers if entry and entry.strip()}
    return sorted(seen) or None


#: Private documents a channel's room may quote, at most. Past it the rest are
#: left out (fail closed) and the reply says so.
MAX_ROOM_DOCUMENTS = 2000


def room_documents(
    org_id: str, member_acls: list[list[str]], shared: list[str]
) -> tuple[list[str], bool]:
    """``doc:<id>`` for each private document EVERY member's ACL opens, and
    whether the list is complete.

    A document ``shared`` already admits (a domain or group all of them are in)
    is not repeated. One overlap per member, bound, on the GIN index; an empty
    member ACL matches nothing, so one unknown member means no document.
    """
    if not member_acls or any(not acl for acl in member_acls):
        return [], True
    from ..db.connection import get_connection

    each = " ".join("AND d.doc_viewers && %s::text[]" for _ in member_acls)
    sql = (
        "SELECT d.id::text FROM documents d WHERE d.org_id = %s::uuid "
        "AND d.doc_is_public IS NOT TRUE AND d.doc_viewers IS NOT NULL "
        "AND NOT d.doc_viewers && %s::text[] " + each + " LIMIT %s"
    )
    with get_connection(org_id=org_id) as conn:
        rows = conn.execute(
            sql, (org_id, shared, *member_acls, MAX_ROOM_DOCUMENTS + 1)
        ).fetchall()
    ids = [f"doc:{row[0]}" for row in rows[:MAX_ROOM_DOCUMENTS]]
    return ids, len(rows) <= MAX_ROOM_DOCUMENTS
