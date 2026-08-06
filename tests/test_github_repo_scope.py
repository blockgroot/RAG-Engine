"""The installation's authorized repo scope (Plan Phase 4, decision D5b).

Two things are proven here, and the second is a security boundary:

1. **We record what the admin actually authorized**, not an assumption.
   "Connect GitHub" does not grant every repo — the admin picks "All
   repositories" or a specific subset on GitHub's own install screen, and
   ``repository_selection`` tells us which. Storing that (with each repo's
   description/topics) mirrors how a Drive connection stores its picked folder.

2. **``resolve_repo`` is the allowlist** every live call must pass a
   model-supplied repo name through. It is the live path's equivalent of
   ``WHERE org_id = ...``: without it, an LLM that hallucinates (or is coaxed
   into naming) ``other-org/secrets`` would send a real authenticated request
   for someone else's repository.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import SourceError
from app.githublive.repos import (
    InstallationScope,
    RepoRef,
    fetch_installation_repos,
    resolve_repo,
    scope_from_config,
    scope_to_config,
)


class _FakeResponse:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def _page(selection: str, repos: list[dict], total: int | None = None) -> dict:
    return {
        "total_count": total if total is not None else len(repos),
        "repository_selection": selection,
        "repositories": repos,
    }


def _repo(full_name: str, description=None, topics=None) -> dict:
    return {
        "full_name": full_name,
        "name": full_name.split("/")[-1],
        "description": description,
        "topics": topics or [],
    }


# -- fetch -----------------------------------------------------------------


def test_fetch_records_all_repositories_selection(monkeypatch):
    monkeypatch.setattr(
        "app.githublive.repos.httpx.get",
        lambda url, headers=None, params=None, timeout=None: _FakeResponse(
            _page("all", [_repo("acme-inc/payments-svc")])
        ),
    )

    selection, repos = fetch_installation_repos("ghs_token")

    assert selection == "all"
    assert [r.full_name for r in repos] == ["acme-inc/payments-svc"]


def test_fetch_records_exactly_the_selected_repositories(monkeypatch):
    """The honest-scope case: never widen 'selected' into 'everything'."""
    monkeypatch.setattr(
        "app.githublive.repos.httpx.get",
        lambda url, headers=None, params=None, timeout=None: _FakeResponse(
            _page("selected", [_repo("acme-inc/handbook"), _repo("acme-inc/payments-svc")])
        ),
    )

    selection, repos = fetch_installation_repos("ghs_token")

    assert selection == "selected"
    assert {r.full_name for r in repos} == {"acme-inc/handbook", "acme-inc/payments-svc"}


def test_fetch_keeps_description_and_topics(monkeypatch):
    """These are what let the model pick a repo with no embeddings at all."""
    monkeypatch.setattr(
        "app.githublive.repos.httpx.get",
        lambda url, headers=None, params=None, timeout=None: _FakeResponse(
            _page(
                "all",
                [_repo("acme-inc/payments-svc", "Billing and invoicing", ["go", "billing"])],
            )
        ),
    )

    _, repos = fetch_installation_repos("ghs_token")

    assert repos[0].description == "Billing and invoicing"
    assert repos[0].topics == ("go", "billing")


def test_fetch_follows_pagination(monkeypatch):
    pages = [
        _page("all", [_repo(f"acme-inc/r{i}") for i in range(100)], total=101),
        _page("all", [_repo("acme-inc/last")], total=101),
    ]
    seen_pages: list = []

    def _get(url, headers=None, params=None, timeout=None):
        seen_pages.append(params.get("page"))
        return _FakeResponse(pages[len(seen_pages) - 1])

    monkeypatch.setattr("app.githublive.repos.httpx.get", _get)

    _, repos = fetch_installation_repos("ghs_token")

    assert len(repos) == 101
    assert repos[-1].full_name == "acme-inc/last"
    assert seen_pages == [1, 2]


def test_fetch_wraps_transport_failures(monkeypatch):
    import httpx

    def _boom(url, headers=None, params=None, timeout=None):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("app.githublive.repos.httpx.get", _boom)

    with pytest.raises(SourceError):
        fetch_installation_repos("ghs_token")


# -- config round trip -----------------------------------------------------


def test_scope_survives_a_config_round_trip():
    scope = InstallationScope(
        installation_id="4242",
        account_login="acme-inc",
        repository_selection="selected",
        repos=(RepoRef("acme-inc/handbook", "Eng handbook", ("docs",)),),
    )

    restored = scope_from_config(scope_to_config(scope))

    assert restored == scope


def test_scope_from_legacy_config_without_repo_list():
    """A connection saved before Phase 4 has no repo list — don't crash on it."""
    scope = scope_from_config({"installation_id": "4242", "account_login": "acme-inc"})

    assert scope.installation_id == "4242"
    assert scope.repos == ()
    # Unknown selection must NOT be optimistically treated as "all".
    assert scope.repository_selection == "selected"


# -- resolve_repo: the allowlist -------------------------------------------


def _selected_scope() -> InstallationScope:
    return InstallationScope(
        installation_id="4242",
        account_login="acme-inc",
        repository_selection="selected",
        repos=(RepoRef("acme-inc/handbook"), RepoRef("acme-inc/payments-svc")),
    )


def _all_scope() -> InstallationScope:
    return InstallationScope(
        installation_id="4242",
        account_login="acme-inc",
        repository_selection="all",
        repos=(RepoRef("acme-inc/handbook"),),
    )


def test_resolve_accepts_an_authorized_repo():
    assert resolve_repo(_selected_scope(), "acme-inc/handbook") == "acme-inc/handbook"


def test_resolve_qualifies_a_bare_repo_name_with_the_account():
    """The model will often say "handbook", not "acme-inc/handbook"."""
    assert resolve_repo(_selected_scope(), "handbook") == "acme-inc/handbook"


def test_resolve_refuses_a_repo_outside_a_selected_installation():
    with pytest.raises(SourceError) as excinfo:
        resolve_repo(_selected_scope(), "acme-inc/secret-payroll")

    assert "not authorized" in str(excinfo.value).lower()


def test_resolve_refuses_a_foreign_owner_even_when_selection_is_all():
    """"All repositories" means all of THIS account's repos, not of GitHub."""
    with pytest.raises(SourceError) as excinfo:
        resolve_repo(_all_scope(), "other-org/secrets")

    assert "other-org" in str(excinfo.value)


def test_resolve_allows_any_repo_of_the_account_when_selection_is_all():
    """Under "all", a repo created after connect is in scope without reconnecting."""
    assert resolve_repo(_all_scope(), "acme-inc/brand-new") == "acme-inc/brand-new"


def test_resolve_is_case_insensitive_like_github():
    assert resolve_repo(_selected_scope(), "ACME-INC/Handbook") == "acme-inc/handbook"


@pytest.mark.parametrize("bad", ["", "   ", "a/b/c", "../../etc/passwd", "acme-inc/", "/handbook"])
def test_resolve_rejects_malformed_repo_arguments(bad):
    """The repo string reaches us from an LLM, so treat it as hostile input."""
    with pytest.raises(SourceError):
        resolve_repo(_selected_scope(), bad)
