"""Act mode, Phase 2: Notion and Google Docs (docs/plans/2026-10-13-write-actions.md).

Pinned here:
- a Notion page is found by its link or an exact, unique title the asker
  wrote; a page Handbook indexed company-wide must be one the asker may read;
- Notion writes fit Notion's limits (2,000 characters per text object, 100
  blocks), comments carry the person's name, and a refused page says how to
  share it; the audience is always unknown (no permission API);
- a new Google Doc is private to the asker, so private sources stay narrower,
  and it is uploaded as a Doc in one request;
- the asker never counts as an outsider of their own sources;
- connecting Notion goes through the SEPARATE Act connection and only into the
  company's Notion workspace; Google only as the person's own work address.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.actions import ActionContext, Audience, Source, assess, drive, notion
from app.actions.exposure import NARROWER, WIDER
from app.auth import OAuthTokens, create_admin, create_session_token, save_connection
from app.auth.user_connections import get_user_token
from app.core.exceptions import ActionRejected
from app.db import get_connection
from app.vectorstore.base import Viewer

from .conftest import requires_db

PAGE_ID = "1f2e3d4c5b6a79881726354433221100"
DASHED = "1f2e3d4c-5b6a-7988-1726-354433221100"


def _ctx(question, *, org="org", viewer=None, sources=()):
    return ActionContext(org, None, "user", "a@x.com", question, "tok", tuple(sources), viewer)


def _page(title, page_id=DASHED, **extra):
    return {"id": page_id, "url": f"https://www.notion.so/{page_id}",
            "properties": {"Name": {"type": "title", "title": [{"plain_text": title}]}}, **extra}


class FakeNotion:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, method, path, token, *, json=None):
        self.calls.append((method, path, json))
        if path == "/search":
            return {"results": self.results}
        if path.startswith("/pages/") and method == "GET":
            return self.results[0]
        if path == "/comments":
            return {"id": "c1"}
        if path.endswith("/children"):
            return {"results": [{"id": "b1"}]}
        if path == "/pages":
            return {"id": "p2", "url": "https://www.notion.so/p2"}
        raise AssertionError(path)


@pytest.fixture
def unindexed(monkeypatch):
    monkeypatch.setattr(notion, "_check_indexed_visibility", lambda page_id, ctx: None)


# --------------------------------------------------------------------------
# Notion (API faked)
# --------------------------------------------------------------------------


def test_a_page_is_found_by_its_exact_title(monkeypatch, unindexed):
    fake = FakeNotion([_page("Launch notes"), _page("Launch notes v2", "x")])
    monkeypatch.setattr(notion, "_call", fake)
    proposal = notion.NotionComment().prepare(
        {"page": "launch notes", "body": "Shipped."}, _ctx("comment on Launch notes: shipped"))
    assert proposal.target.external_id == DASHED
    assert proposal.audience == Audience(members=None)  # Notion cannot list readers


def test_a_page_link_is_used_directly(monkeypatch, unindexed):
    fake = FakeNotion([_page("Roadmap")])
    monkeypatch.setattr(notion, "_call", fake)
    link = f"https://www.notion.so/acme/Roadmap-{PAGE_ID}"
    notion.NotionAppend().prepare({"page": link, "body": "Q4 done."},
                                  _ctx(f"add to {link}: Q4 done"))
    assert fake.calls[0][:2] == ("GET", f"/pages/{PAGE_ID}")


def test_two_pages_with_the_same_title_need_a_link(monkeypatch, unindexed):
    monkeypatch.setattr(notion, "_call", FakeNotion([_page("Notes"), _page("Notes", "y")]))
    with pytest.raises(ActionRejected, match="Paste the page's link"):
        notion.NotionComment().prepare({"page": "Notes", "body": "b"}, _ctx("comment on Notes"))


def test_a_page_named_only_in_a_document_is_refused(monkeypatch, unindexed):
    fake = FakeNotion([_page("Board minutes")])
    monkeypatch.setattr(notion, "_call", fake)
    with pytest.raises(ActionRejected, match="Name the Notion page"):
        notion.NotionComment().prepare({"page": "Board minutes", "body": "b"},
                                       _ctx("comment on the launch page"))
    assert fake.calls == []


def test_a_trashed_page_is_refused(monkeypatch, unindexed):
    monkeypatch.setattr(notion, "_call", FakeNotion([_page("Old", in_trash=True)]))
    with pytest.raises(ActionRejected, match="trash"):
        notion.NotionComment().prepare({"page": "Old", "body": "b"}, _ctx("comment on Old"))


def test_notion_writes_fit_notions_limits_and_name_the_person(monkeypatch):
    fake = FakeNotion([])
    monkeypatch.setattr(notion, "_call", fake)
    target = {"external_id": DASHED, "url": "https://www.notion.so/x"}

    notion.NotionComment().execute("k", target, {"body": "y" * 4500}, "tok")
    _m, _p, body = fake.calls[-1]
    assert body["display_name"] == {"type": "user"}
    assert [len(t["text"]["content"]) for t in body["rich_text"]] == [2000, 2000, 500]

    many = "\n\n".join(f"para {i}" for i in range(130))
    notion.NotionAppend().execute("k", target, {"body": many}, "tok")
    children = fake.calls[-1][2]["children"]
    assert len(children) == notion.BLOCK_LIMIT
    assert "para 129" in children[-1]["paragraph"]["rich_text"][0]["text"]["content"]

    made = notion.NotionCreatePage().execute("k", target, {"title": "Retro", "body": "Went well."}, "tok")
    _m, path, body = fake.calls[-1]
    assert path == "/pages" and body["parent"] == {"page_id": DASHED}
    assert body["properties"]["title"]["title"][0]["text"]["content"] == "Retro"
    assert made.url == "https://www.notion.so/p2"


def test_page_writes_do_not_claim_to_be_the_person():
    assert notion.NotionComment.attribution == "as you"
    assert "Handbook Actions" in notion.NotionAppend.attribution
    assert "Handbook Actions" in notion.NotionCreatePage.attribution


def test_a_refused_page_says_how_to_share_it(monkeypatch):
    class Resp:
        status_code = 404

        def json(self):
            return {"code": "object_not_found"}

    monkeypatch.setattr(notion.httpx, "request", lambda *a, **k: Resp())
    with pytest.raises(ActionRejected, match="Connections"):
        notion._call("GET", f"/pages/{PAGE_ID}", "tok")


# --------------------------------------------------------------------------
# Google Docs (API faked)
# --------------------------------------------------------------------------


def test_a_new_doc_is_private_to_the_asker():
    secret = Source("d1", "Reorg plan", public=False, viewers=("someone@x.com",))
    proposal = drive.DriveCreateDoc().prepare(
        {"title": "Reorg summary", "body": "Notes."}, _ctx("make a doc summarising the reorg"))
    assert proposal.audience == Audience(members=())
    # The asker reads the plan through a group, not by name: still not an outsider.
    assert assess([secret], proposal.audience, asker="a@x.com").verdict == NARROWER


def test_the_asker_is_never_their_own_outsider():
    secret = Source("d1", "Plan", public=False, viewers=("group:eng",))
    assert assess([secret], Audience(members=("A@x.com",)), asker="a@x.com").verdict == NARROWER
    assert assess([secret], Audience(members=("a@x.com", "b@x.com")), asker="a@x.com").verdict == WIDER


def test_the_doc_is_uploaded_as_a_google_doc(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200

        def json(self):
            return {"id": "doc1", "webViewLink": "https://docs.google.com/document/d/doc1"}

    def fake_post(url, params=None, content=None, headers=None, timeout=None):
        seen.update(url=url, params=params, content=content.decode(), headers=headers)
        return Resp()

    monkeypatch.setattr(drive.httpx, "post", fake_post)
    made = drive.DriveCreateDoc().execute("k", {}, {"title": "Retro", "body": "Went well."}, "tok")
    assert made.url == "https://docs.google.com/document/d/doc1"
    assert seen["params"]["uploadType"] == "multipart"
    assert '"mimeType": "application/vnd.google-apps.document"' in seen["content"]
    assert "Went well." in seen["content"]
    assert seen["headers"]["Content-Type"].startswith("multipart/related; boundary=")


# --------------------------------------------------------------------------
# Real database: indexed visibility, the connect flows
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
    monkeypatch.setenv("ACTIONS_ENABLED", "true")
    monkeypatch.setenv("NOTION_CLIENT_ID", "read-cid")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "read-secret")
    monkeypatch.setenv("NOTION_REDIRECT_URI", "https://api.example.com/auth/notion/callback")
    monkeypatch.setenv("NOTION_ACT_CLIENT_ID", "act-cid")
    monkeypatch.setenv("NOTION_ACT_CLIENT_SECRET", "act-secret")
    monkeypatch.setenv("NOTION_ACT_REDIRECT_URI", "https://api.example.com/auth/notion/callback")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "g-cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "g-secret")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://api.example.com/auth/google/callback")


@pytest.fixture
def org(store, org_cleanup):
    org_id = store.create_organization(f"Act2 {uuid.uuid4().hex[:8]}")
    org_cleanup.append(org_id)
    admin = create_admin(f"act2-{uuid.uuid4().hex[:8]}@example.com", org_id)
    return SimpleNamespace(id=org_id, admin=admin)


@requires_db
def test_an_indexed_page_the_asker_cannot_read_is_refused(org, monkeypatch):
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO documents (org_id, title, source_provider, source_external_id, "
            "doc_is_public, doc_viewers) VALUES (%s, 'Board minutes', 'notion', %s, false, %s)",
            (org.id, DASHED, ["ceo@x.com"]),
        )
    monkeypatch.setattr(notion, "_call", FakeNotion([_page("Board minutes")]))
    with pytest.raises(ActionRejected, match="can't edit"):
        notion.NotionComment().prepare(
            {"page": "Board minutes", "body": "b"},
            _ctx("comment on Board minutes", org=org.id, viewer=Viewer(email="a@x.com")))
    # The CEO, who can read it in Handbook, is not stopped by Handbook.
    notion.NotionComment().prepare(
        {"page": "Board minutes", "body": "b"},
        _ctx("comment on Board minutes", org=org.id, viewer=Viewer(email="ceo@x.com")))


@pytest.fixture
def client():
    from app.api.main import create_app

    return TestClient(create_app())


def _start(client, org, provider):
    start = client.get(f"/act/connect/{provider}", cookies={"session": create_session_token(org.admin)},
                       follow_redirects=False)
    assert start.status_code in (302, 307), start.text
    location = start.headers["location"]
    return location, location.split("state=")[1].split("&")[0]


@requires_db
def test_notion_connects_through_the_act_connection_into_the_company_workspace(client, org, monkeypatch):
    from app.auth.notion_oauth import NotionOAuthProvider

    save_connection(org.id, "notion", OAuthTokens("read_tok", None, None, "company-ws", "Acme"))
    location, state = _start(client, org, "notion")
    assert "client_id=act-cid" in location  # never the read connection's client

    used = {}

    def exchange(self, code):
        used["client"] = self._client_id
        return OAuthTokens("ntn_act", None, None, "company-ws", "Acme")

    monkeypatch.setattr(NotionOAuthProvider, "exchange_code", exchange)
    monkeypatch.setattr("app.api.actions._notion_owner", lambda token: ("nu-1", "Ada"))
    done = client.get(f"/auth/notion/callback?code=c&state={state}", follow_redirects=False)
    assert "connected=notion" in done.headers["location"]
    assert used["client"] == "act-cid"
    assert get_user_token(org.id, org.admin.id, "notion").access_token == "ntn_act"


@requires_db
def test_a_personal_notion_workspace_is_refused(client, org, monkeypatch):
    from app.auth.notion_oauth import NotionOAuthProvider

    save_connection(org.id, "notion", OAuthTokens("read_tok", None, None, "company-ws", "Acme"))
    _location, state = _start(client, org, "notion")
    monkeypatch.setattr(NotionOAuthProvider, "exchange_code",
                        lambda self, code: OAuthTokens("ntn", None, None, "my-own-ws", "Me"))
    done = client.get(f"/auth/notion/callback?code=c&state={state}", follow_redirects=False)
    assert "connect_error=notion&reason=workspace" in done.headers["location"]


@requires_db
def test_google_asks_for_drive_file_only_and_only_the_work_account(client, org, monkeypatch):
    from app.auth.google_oauth import GoogleOAuthProvider

    location, state = _start(client, org, "google")
    assert "drive.file" in location and "drive.readonly" not in location
    assert "include_granted_scopes=false" in location

    monkeypatch.setattr(GoogleOAuthProvider, "exchange_code",
                        lambda self, code: OAuthTokens("ya29", "r", None, "me@gmail.com", "Me"))
    done = client.get(f"/auth/google/callback?code=c&state={state}", follow_redirects=False)
    assert "connect_error=google&reason=account" in done.headers["location"]

    _location, state = _start(client, org, "google")
    monkeypatch.setattr(GoogleOAuthProvider, "exchange_code",
                        lambda self, code: OAuthTokens("ya29", "r", None, org.admin.email.upper(), "Ada"))
    done = client.get(f"/auth/google/callback?code=c&state={state}", follow_redirects=False)
    assert "connected=google" in done.headers["location"]
    assert get_user_token(org.id, org.admin.id, "google").scopes == (drive.DRIVE_FILE,)


@requires_db
def test_status_says_which_providers_are_set_up(client, org, monkeypatch):
    monkeypatch.delenv("NOTION_ACT_CLIENT_ID")
    body = client.get("/act/status", cookies={"session": create_session_token(org.admin)}).json()
    available = {p["provider"]: p["available"] for p in body["providers"]}
    assert available["notion"] is False and available["google"] is True
    assert json.dumps(body)  # serializable
