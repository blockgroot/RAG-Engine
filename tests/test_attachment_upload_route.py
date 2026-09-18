"""Uploading several files: some land, some come back named with a reason.

This is the behaviour a loop-and-stop upload got wrong, and it is worth its own
file because the failure is silent from the server's side: the request
succeeds, the member just never gets the four good documents they picked. Each
test here selects a mix on purpose, because a test that uploads one file can
pass while the partial path is broken.

The object store is faked -- these are decisions in `api/attachments.py`, not
Cloudinary's API.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.attachments import store as attach_store
from app.db import get_connection
from .conftest import requires_db
from .test_attachment_storage import FakeBlobStore

pytestmark = requires_db


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")


@pytest.fixture
def client(org_cleanup, monkeypatch):
    from app.api.main import create_app
    from app.auth import create_admin, create_session_token

    monkeypatch.setattr(attach_store, "blobstore", FakeBlobStore())

    with get_connection() as conn:
        org = str(
            conn.execute(
                "INSERT INTO organizations (name) VALUES (%s) RETURNING id",
                (f"upload-{uuid.uuid4().hex[:8]}",),
            ).fetchone()[0]
        )
        conn.commit()
    org_cleanup.append(org)

    user = create_admin(f"upload-{uuid.uuid4().hex[:8]}@example.com", org)
    token = create_session_token(user)

    with get_connection() as conn:
        conv = str(
            conn.execute(
                "INSERT INTO conversations (org_id, user_id) VALUES (%s, %s) RETURNING id",
                (org, user.id),
            ).fetchone()[0]
        )
        conn.commit()

    return TestClient(create_app()), {"session": token}, conv


def _post(client, cookies, conv, files):
    return client.post(
        f"/chat/conversations/{conv}/attachments",
        files=[("file", (name, body, ctype)) for name, body, ctype in files],
        cookies=cookies,
    )


def test_a_bad_file_does_not_cost_the_good_ones(client):
    """The whole point: four documents and one scan is four documents."""
    c, cookies, conv = client
    response = _post(
        c,
        cookies,
        conv,
        [
            ("notes.txt", b"the notice period is 30 days", "text/plain"),
            ("photo.heic", b"\x00\x01binary", "image/heic"),
            ("readme.md", b"# Handbook\n\nLeave policy lives here.", "text/markdown"),
        ],
    )
    assert response.status_code == 200
    body = response.json()

    assert [a["filename"] for a in body["attachments"]] == ["notes.txt", "readme.md"]
    assert [r["filename"] for r in body["rejected"]] == ["photo.heic"]
    # The reason names the file and what is supported -- "rejected" alone
    # sends someone re-uploading to find out why.
    assert "photo.heic" in body["rejected"][0]["reason"]


def test_an_empty_file_is_named_not_swallowed(client):
    c, cookies, conv = client
    body = _post(c, cookies, conv, [("blank.txt", b"", "text/plain")]).json()

    assert body["attachments"] == []
    assert body["rejected"][0]["reason"] == "blank.txt is empty"


def test_a_file_too_big_for_a_prompt_is_refused_before_storage(client, monkeypatch):
    """The token gate: no point storing what can never reach a prompt."""
    monkeypatch.setenv("ATTACHMENT_MAX_TOKENS", "50")
    c, cookies, conv = client
    body = _post(
        c, cookies, conv, [("long.txt", b"word " * 5000, "text/plain")]
    ).json()

    assert body["attachments"] == []
    assert "over the 50 limit" in body["rejected"][0]["reason"]


def test_a_spreadsheet_skips_the_token_gate(client, monkeypatch):
    """Onyx's tabular bypass: a CSV is legitimately long."""
    monkeypatch.setenv("ATTACHMENT_MAX_TOKENS", "50")
    c, cookies, conv = client
    rows = b"name,days\n" + b"".join(b"ada,25\n" for _ in range(2000))
    body = _post(c, cookies, conv, [("leave.csv", rows, "text/csv")]).json()

    assert body["rejected"] == []
    assert body["attachments"][0]["filename"] == "leave.csv"


def test_the_per_chat_cap_rejects_the_overflow_only(client, monkeypatch):
    """Files up to the cap still land; the rest are named."""
    monkeypatch.setenv("ATTACHMENT_MAX_PER_CONVERSATION", "2")
    c, cookies, conv = client
    body = _post(
        c,
        cookies,
        conv,
        [
            ("one.txt", b"first", "text/plain"),
            ("two.txt", b"second", "text/plain"),
            ("three.txt", b"third", "text/plain"),
        ],
    ).json()

    assert [a["filename"] for a in body["attachments"]] == ["one.txt", "two.txt"]
    assert [r["filename"] for r in body["rejected"]] == ["three.txt"]


def test_another_members_conversation_is_still_a_404(client):
    """Per-file reasons must not have softened the request-level checks."""
    c, cookies, _ = client
    response = _post(
        c, cookies, str(uuid.uuid4()), [("notes.txt", b"hello", "text/plain")]
    )
    assert response.status_code == 404
