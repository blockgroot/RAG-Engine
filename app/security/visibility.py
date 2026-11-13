"""The ONE spelling of "may this viewer read this document?".

``doc_is_public OR doc_viewers && acl`` (CLAUDE.md §3). Every reader
(retrieval, chips, digests, the graph walk) splices this fragment so they
cannot disagree.

* The ACL array is a BOUND parameter, never formatted in (entries are emails).
* ``doc_viewers && '{}'`` is FALSE, so an empty ACL means scope-public only,
  which is what makes ``Viewer.public_only_viewer()`` safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the vector store imports this module; avoid a cycle
    from ..vectorstore.base import Viewer

_PREDICATE = "({prefix}doc_is_public OR {prefix}doc_viewers && {param}::text[])"


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
