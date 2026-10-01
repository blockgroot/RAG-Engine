"""Per-asker GitHub repository access (`githublive.access`, CLAUDE.md §3).

The installation token reads every granted repository, so a private one was
answerable to anyone in the scope. A public repository stays open to all; a
private one needs the asker's LINKED GitHub login to have access on GitHub.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import SourceError
from app.githublive import access
from app.githublive.base import GitHubReader
from app.githublive.repos import InstallationScope, RepoRef, scope_from_config, scope_to_config
from app.vectorstore.base import Viewer

PUB = RepoRef("acme/docs", private=False)
SECRET = RepoRef("acme/secret", description="acquisition", private=True)
OLD = RepoRef("acme/legacy")  # stored before `private` was recorded


class _Fake(GitHubReader):
    def __init__(self, repos, perms=None, fail=False):
        self._repos, self._perms, self._fail, self.checks = repos, perms or {}, fail, []

    def list_repos(self):
        return list(self._repos)

    def repo_permission(self, full_name, login):
        self.checks.append(full_name)
        if self._fail:
            raise RuntimeError("github down")
        return self._perms.get(full_name, "none")

    def get_readme(self, repo):
        return f"readme of {repo}"

    def get_commit(self, repo, sha): ...
    def list_commits(self, repo, **kw): return []
    def list_pull_requests(self, repo, **kw): ...
    def get_pull_request(self, repo, pull_number): ...
    def list_branches(self, repo, *, limit=50): return []
    def list_reviews(self, repo, pull_number): return []


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    access._cache.clear()
    monkeypatch.setattr(access, "linked_login", lambda org, email: None)


def _as(monkeypatch, login):
    monkeypatch.setattr(access, "linked_login", lambda org, email: login)


def test_an_unrestricted_caller_reads_everything():
    fake = _Fake([PUB, SECRET])
    assert access.restrict(fake, "o", Viewer.unrestricted()) is fake
    assert access.restrict(fake, "o", None) is fake


def test_without_a_linked_login_only_public_repos_are_offered():
    reader = access.restrict(_Fake([PUB, SECRET, OLD]), "o", Viewer(email="ada@acme.com"))
    assert [r.full_name for r in reader.list_repos()] == ["acme/docs"]
    with pytest.raises(SourceError):
        reader.get_readme("acme/secret")
    with pytest.raises(SourceError):
        reader.get_readme("secret")  # a bare name cannot sneak past the catalog


def test_a_linked_login_reads_the_private_repos_github_lets_it(monkeypatch):
    _as(monkeypatch, "ada")
    fake = _Fake([PUB, SECRET, OLD], perms={"acme/secret": "read"})
    reader = access.restrict(fake, "o", Viewer(email="ada@acme.com"))
    assert [r.full_name for r in reader.list_repos()] == ["acme/docs", "acme/secret"]
    assert reader.get_readme("secret") == "readme of secret"
    assert "acme/docs" not in fake.checks  # a public repo costs no call
    assert reader.hidden == 1  # the unrecorded one is treated as private and denied


def test_a_failed_check_hides_the_repo_and_is_not_remembered(monkeypatch):
    _as(monkeypatch, "ada")
    viewer = Viewer(email="ada@acme.com")
    assert access.restrict(_Fake([SECRET], fail=True), "o", viewer).list_repos() == []
    ok = _Fake([SECRET], perms={"acme/secret": "write"})
    assert access.restrict(ok, "o", viewer).list_repos() == [SECRET]


def test_a_slack_channel_reply_reads_public_repos_only(monkeypatch):
    _as(monkeypatch, "ada")  # even a linked asker: the ROOM reads the reply
    reader = access.restrict(_Fake([SECRET], perms={"acme/secret": "admin"}), "o",
                             Viewer.public_only_viewer())
    assert reader.list_repos() == []
    assert "direct message" in access.restricted_message(reader)


def test_the_refusal_says_what_to_do(monkeypatch):
    reader = access.restrict(_Fake([SECRET]), "o", Viewer(email="ada@acme.com"))
    assert "Linked accounts" in access.restricted_message(reader)
    _as(monkeypatch, "ada")
    reader = access.restrict(_Fake([SECRET]), "o", Viewer(email="ada@acme.com"))
    assert "@ada" in access.restricted_message(reader)
    reader = access.restrict(_Fake([PUB, SECRET]), "o", Viewer(email="ada@acme.com"))
    assert access.restricted_message(reader) is None  # something is visible


def test_the_private_flag_survives_the_stored_scope():
    scope = InstallationScope("1", "acme", "selected", (PUB, SECRET, OLD))
    assert scope_from_config(scope_to_config(scope)).repos == (PUB, SECRET, OLD)


def test_the_agent_refuses_without_a_model_call():
    from app.agent.github_agent import GitHubAgent

    class _NoLLM:
        def __getattr__(self, name):
            raise AssertionError("no model call expected")

    agent = GitHubAgent(_NoLLM(), lambda org, ws: _Fake([SECRET]), "fallback")
    response = agent.answer("what changed in secret?", "o", viewer=Viewer(email="ada@acme.com"))
    assert response.access_restricted and "Linked accounts" in response.answer
