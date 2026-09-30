"""Nested Google Group expansion (`sources.google_groups._nested_groups`).

`groups.list?userKey=` returns DIRECT memberships only, so a file shared with
`all-staff@` was withheld from everyone who is in it through `eng@`. These pin
the `hasMember` pass that closes that, and that every failure still fails
closed -- a partial answer is never cached for the TTL.
"""

from __future__ import annotations

import pytest

from app.sources import google_groups


class _Resp:
    def __init__(self, status: int, body: dict | None = None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    google_groups.clear_cache()
    monkeypatch.setenv("GOOGLE_GROUPS_ENABLED", "true")
    monkeypatch.setattr(
        "app.auth.credentials.get_live_connection_token", lambda *a, **k: "tok"
    )
    yield
    google_groups.clear_cache()


def _directory(monkeypatch, *, direct, nested_answers, candidates):
    """Fake the listing, the candidate query and `hasMember`, recording calls."""
    calls: list[str] = []
    monkeypatch.setattr(google_groups, "_fetch_groups", lambda *a, **k: direct)
    monkeypatch.setattr(google_groups, "_candidate_groups", lambda org, domain: candidates)

    def _get(url, **kwargs):
        calls.append(url)
        group = url.split("/groups/", 1)[1].split("/hasMember/", 1)[0]
        answer = nested_answers[group]
        return answer if isinstance(answer, _Resp) else _Resp(200, {"isMember": answer})

    monkeypatch.setattr(google_groups.httpx, "get", _get)
    return calls


def test_an_indirect_membership_is_found(monkeypatch):
    _directory(
        monkeypatch,
        direct=("eng@corp.com",),
        candidates=["all-staff@corp.com", "eng@corp.com", "finance@corp.com"],
        nested_answers={"all-staff@corp.com": True, "finance@corp.com": False},
    )
    assert google_groups.groups_for("org", "ada@corp.com") == (
        "all-staff@corp.com", "eng@corp.com",
    )


def test_a_direct_group_is_not_asked_about_again(monkeypatch):
    calls = _directory(
        monkeypatch,
        direct=("eng@corp.com",),
        candidates=["eng@corp.com"],
        nested_answers={},
    )
    assert google_groups.groups_for("org", "ada@corp.com") == ("eng@corp.com",)
    assert calls == []


def test_a_failed_check_withholds_that_group_and_is_not_cached(monkeypatch):
    """One `hasMember` 500 must not cache a partial list for ten minutes --
    that would lock someone out of every document shared with that group."""
    calls = _directory(
        monkeypatch,
        direct=(),
        candidates=["all-staff@corp.com"],
        nested_answers={"all-staff@corp.com": _Resp(500)},
    )
    assert google_groups.groups_for("org", "ada@corp.com") == ()
    google_groups.groups_for("org", "ada@corp.com")
    assert len(calls) == 2  # asked again: the failure was not cached


def test_a_cross_domain_invalid_input_is_a_real_no_and_is_cached(monkeypatch):
    """Google answers nesting across domains with 400 `Invalid input`. That is
    an answer ("not reachable this way"), so it may be cached."""
    calls = _directory(
        monkeypatch,
        direct=(),
        candidates=["partners@corp.com"],
        nested_answers={"partners@corp.com": _Resp(400)},
    )
    assert google_groups.groups_for("org", "ada@corp.com") == ()
    google_groups.groups_for("org", "ada@corp.com")
    assert len(calls) == 1


def test_unreadable_candidates_fall_back_to_direct_groups_uncached(monkeypatch):
    _directory(monkeypatch, direct=("eng@corp.com",), candidates=None, nested_answers={})
    assert google_groups.groups_for("org", "ada@corp.com") == ("eng@corp.com",)
    assert google_groups._cached(("org", "ada@corp.com")) is None


def test_has_member_url_escapes_the_keys(monkeypatch):
    seen = []
    monkeypatch.setattr(
        google_groups.httpx, "get",
        lambda url, **k: seen.append(url) or _Resp(200, {"isMember": True}),
    )
    assert google_groups._has_member("tok", "a/b@corp.com", "ada@corp.com") is True
    assert seen == [
        "https://admin.googleapis.com/admin/directory/v1/groups/a%2Fb@corp.com"
        "/hasMember/ada@corp.com"
    ]
