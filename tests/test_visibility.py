"""The document-access predicate has exactly ONE spelling.

It was written out by hand in three places (vector store, starter chips,
scheduler digest). These tests pin the shared fragment and fail the moment a
hand-written copy reappears anywhere in `app/`, because a filter spelled twice
is a filter that is eventually wrong in one of the two places.
"""

from __future__ import annotations

from pathlib import Path

from app.security.visibility import normalize_viewers, viewer_clause, visibility_predicate
from app.vectorstore.base import Viewer

APP = Path(__file__).resolve().parent.parent / "app"


def test_the_predicate_binds_the_acl_and_never_formats_it():
    assert visibility_predicate("d") == "(d.doc_is_public OR d.doc_viewers && %s::text[])"
    assert visibility_predicate("fd") == "(fd.doc_is_public OR fd.doc_viewers && %s::text[])"
    assert visibility_predicate(None) == "(doc_is_public OR doc_viewers && %s::text[])"


def test_an_unrestricted_viewer_adds_no_clause_at_all():
    """The ABSENCE of a clause, so every pre-access read is byte-identical."""
    assert viewer_clause(None) == ("", [])
    assert viewer_clause(Viewer.unrestricted()) == ("", [])


def test_a_person_gets_the_clause_and_their_acl():
    sql, params = viewer_clause(Viewer(email="Ada@Example.com"), alias="fd")
    assert sql == " AND (fd.doc_is_public OR fd.doc_viewers && %s::text[])"
    assert params == [Viewer(email="Ada@Example.com").acl()]


def test_public_only_binds_an_empty_acl_not_nothing():
    """`doc_viewers && '{}'` is FALSE: an empty ACL is scope-public only."""
    sql, params = viewer_clause(Viewer.public_only_viewer())
    assert sql and params == [[]]


def test_viewers_are_normalized_on_write():
    assert normalize_viewers([" Ada@X.com", "ada@x.com", "", "bob@x.com"]) == [
        "ada@x.com",
        "bob@x.com",
    ]
    assert normalize_viewers([]) is None


def test_no_module_spells_the_predicate_by_hand():
    """Every reader must splice `visibility_predicate`. A hand-written copy is
    how retrieval, chips and digests would drift into disagreeing about who
    may read a document."""
    offenders = []
    for path in APP.rglob("*.py"):
        if path.name == "visibility.py":
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if "doc_viewers &&" in code and ("%s" in code or "text[]" in code):
                offenders.append(f"{path.relative_to(APP)}:{lineno}")
    assert offenders == [], f"hand-written access predicate: {offenders}"
