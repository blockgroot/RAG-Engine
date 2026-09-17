"""A repo name must pick the SCOPE its GitHub is connected in.

Measured failure this pins: "What does the chain-guard repository do?" asked in
a Slack DM resolved to org-wide Google Drive and refused, while `Chain-Guard`
sat authorized in a space one scope away. `_named_scope` only ever read
`documents`/`chunks`, and GitHub embeds nothing -- so the space looked empty,
the probe picked the scope with the most prose, and every GitHub rung in
`choose_agent` (guarded on `"github" in connected`) was then skipped.
"""

from __future__ import annotations

from app.agent import routing


class _FakeConn:
    def __init__(self, titles, repos):
        self._titles, self._repos = titles, repos

    def execute(self, sql, params):
        self._rows = self._repos if "oauth_connections" in sql else self._titles
        return self

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _patch_db(monkeypatch, titles, repos):
    import app.db.connection as dbconn

    monkeypatch.setattr(dbconn, "get_connection", lambda: _FakeConn(titles, repos))


SCOPES = [(None, "Company"), ("ws-code", "Engineering")]
REPOS = [("ws-code", [{"full_name": "18-sana/Chain-Guard"}, {"full_name": "18-sana/DAO"}])]


def test_repo_name_picks_the_scope_its_github_lives_in(monkeypatch):
    _patch_db(monkeypatch, titles=[], repos=REPOS)
    assert (
        routing._named_scope("org", SCOPES, "What does the chain-guard repository do?")
        == "ws-code"
    )


def test_owner_slash_name_also_counts(monkeypatch):
    _patch_db(monkeypatch, titles=[], repos=REPOS)
    assert routing._named_scope("org", SCOPES, "status of 18-sana/DAO?") == "ws-code"


def test_an_unnamed_question_still_falls_through_to_the_probe(monkeypatch):
    _patch_db(monkeypatch, titles=[], repos=REPOS)
    assert (
        routing._named_scope("org", SCOPES, "how much leave do I have left?")
        is routing._NO_MATCH
    )


def test_a_substring_is_not_a_repo_name(monkeypatch):
    """The word-edge discipline `_named_repo` already had, now shared."""
    _patch_db(monkeypatch, titles=[], repos=[("ws-code", [{"full_name": "acme/api"}])])
    assert (
        routing._named_scope("org", SCOPES, "how rapidly did we ship?")
        is routing._NO_MATCH
    )


def test_two_scopes_naming_it_resolve_to_neither(monkeypatch):
    """The wrong space is worse than letting the probe decide."""
    _patch_db(
        monkeypatch,
        titles=[],
        repos=[
            ("ws-code", [{"full_name": "a/Chain-Guard"}]),
            ("ws-other", [{"full_name": "b/Chain-Guard"}]),
        ],
    )
    assert (
        routing._named_scope("org", SCOPES, "what is chain-guard?") is routing._NO_MATCH
    )


def test_a_document_title_still_wins_its_scope(monkeypatch):
    """The pre-existing signal is untouched."""
    _patch_db(monkeypatch, titles=[("Meeting_notes_1", "ws-meet")], repos=[])
    assert (
        routing._named_scope("org", SCOPES, "What should I know from Meeting_note_1?")
        == "ws-meet"
    )


# --- rung 3: code intent reaches a scope that has no chunks at all ----------


def _patch_scope_ladder(monkeypatch, probe, repos):
    """Stub the two DB-backed inputs `choose_scope` consults."""
    monkeypatch.setattr(routing, "_named_scope", lambda *a: routing._NO_MATCH)
    monkeypatch.setattr(routing, "_probe_scope_best", lambda *a: probe)
    monkeypatch.setattr(routing, "_github_repo_scopes", lambda *a: repos)


GH = [("ws-code", [{"full_name": "18-sana/Chain-Guard"}])]


def test_code_question_reaches_the_github_scope_when_nothing_clears_the_gate(
    monkeypatch,
):
    _patch_scope_ladder(monkeypatch, probe=(None, 0.21), repos=GH)
    assert routing.choose_scope("org", SCOPES, "who reviewed the auth PR?") == "ws-code"


def test_a_scoring_document_question_still_wins(monkeypatch):
    """Rung 2 is above rung 3: a code word cannot hijack a scope that scores."""
    _patch_scope_ladder(monkeypatch, probe=(None, 0.61), repos=GH)
    assert routing.choose_scope("org", SCOPES, "who approved the budget?") is None


def test_no_code_intent_falls_through_to_the_weak_probe(monkeypatch):
    _patch_scope_ladder(monkeypatch, probe=("ws-meet", 0.19), repos=GH)
    assert routing.choose_scope("org", SCOPES, "how much leave is left?") == "ws-meet"


def test_two_github_scopes_resolve_to_neither(monkeypatch):
    _patch_scope_ladder(
        monkeypatch,
        probe=("ws-meet", 0.19),
        repos=[("ws-a", [{"full_name": "x/one"}]), ("ws-b", [{"full_name": "y/two"}])],
    )
    assert routing.choose_scope("org", SCOPES, "show me the commits") == "ws-meet"


def test_a_github_scope_with_no_authorized_repos_is_not_a_candidate(monkeypatch):
    _patch_scope_ladder(monkeypatch, probe=None, repos=[("ws-code", [])])
    assert routing.choose_scope("org", SCOPES, "show me the commits") is routing._NO_MATCH
