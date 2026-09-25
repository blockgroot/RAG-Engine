"""The ONE spelling of "may this viewer read this document?".

Document-level access filtering is a single conjunct on `documents`:
``doc_is_public OR doc_viewers && acl`` (CLAUDE.md §3). It used to be written
out by hand in three places -- the vector store, starter chips
(`api/chat.py`) and the scheduler's indexed digest (`schedulers/activity.py`)
-- and a filter written out three times is a filter that will one day be
wrong in one of them. Every reader now splices THIS fragment, and the
Second Brain graph walk (docs/plans/2026-09-23-second-brain.md) will too, so
retrieval, chips, digests and graph evidence cannot disagree about a document.

Two rules the fragment encodes, and why they live here rather than at each
call site:

* The ACL array is a BOUND parameter (``%s::text[]``), never formatted in --
  an entry is a user-supplied email.
* ``doc_viewers && '{}'`` is FALSE, so an empty ACL means "scope-public only",
  never "everything". That is what makes ``Viewer.public_only_viewer()`` safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # the vector store imports this module; avoid a cycle
    from ..vectorstore.base import Viewer

_PREDICATE = "({prefix}doc_is_public OR {prefix}doc_viewers && %s::text[])"


def visibility_predicate(alias: str | None = "d") -> str:
    """The bare predicate, parenthesised, with ONE ``%s`` for the ACL array.

    ``alias`` is the `documents` table alias; ``None`` means the columns are
    referenced unqualified (a query that selects from `documents` alone).
    No leading ``AND`` so it can also be negated -- `restricted_match` asks for
    exactly what this predicate removes, so "withheld" can never mean anything
    else.
    """
    prefix = f"{alias}." if alias else ""
    return _PREDICATE.format(prefix=prefix)


_EVIDENCE_PREDICATE = "({prefix}is_public IS TRUE OR {prefix}viewers && %s::text[])"


def evidence_predicate(alias: str = "ev") -> str:
    """The rule for knowledge-graph evidence that is NOT a document.

    A graph edge backed by a document is checked with ``visibility_predicate``
    against that document, live. Evidence with no document -- a GitHub fact, a
    ``same_person`` link -- carries its own ``is_public``/``viewers`` instead.
    Same shape and the same two rules as the document predicate, and one more:
    ``is_public IS TRUE``, so a NULL (never set) is NOT public -- fail closed.
    ONE ``%s`` for the ACL array.
    """
    return _EVIDENCE_PREDICATE.format(prefix=f"{alias}.")


def viewer_clause(viewer: "Viewer | None", alias: str | None = "d") -> tuple[str, list[list[str]]]:
    """Return ``(" AND <predicate>", [acl])`` for ``viewer``, or ``("", [])``.

    An unrestricted viewer yields the literal ABSENCE of a clause, not a clause
    that happens to match everything, so every read that predates
    document-level access is byte-identical at the SQL level.
    """
    if viewer is None or viewer.is_unrestricted:
        return "", []
    return " AND " + visibility_predicate(alias), [viewer.acl()]


def normalize_viewers(viewers: list[str] | None) -> list[str] | None:
    """Lowercase, de-duplicate and drop blanks -- the write side of ``Viewer.acl``.

    Both sides must agree on spelling or a real grant silently stops matching,
    which fails CLOSED (the person is locked out) rather than open. That is the
    safe direction, and also the one nobody reports as a security bug -- so it
    is normalized in exactly one place.
    """
    if not viewers:
        return None
    seen = {entry.strip().lower() for entry in viewers if entry and entry.strip()}
    return sorted(seen) or None
