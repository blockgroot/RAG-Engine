"""Google Integration Phase 4: per-connection ingestion scope config.

Covers ``set_connection_config``/``get_connection_config`` (DB-backed, real
Postgres) and the pure ``extract_drive_folder_id`` parser (no DB needed).
"""

from __future__ import annotations

import pytest

from app.auth import get_connection_config, save_connection, set_connection_config
from app.auth.base import OAuthTokens
from app.core.exceptions import ConfigurationError
from app.sources.google_drive_utils import extract_drive_folder_id

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _auth_encryption_key(monkeypatch):
    """A real Fernet key so save_connection's encrypt() works end to end."""
    from cryptography.fernet import Fernet

    monkeypatch.setenv("AUTH_ENCRYPTION_KEYS", Fernet.generate_key().decode())


# -- DB-backed round trip -------------------------------------------------------


@requires_db
def test_set_and_get_connection_config_round_trip(store, org_cleanup):
    org_id = store.create_organization("Source Config Test Org")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="goog_access",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="drive-user@example.com",
        ),
    )

    assert get_connection_config(org_id, "google") is None

    set_connection_config(org_id, "google", {"folder_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz"})

    assert get_connection_config(org_id, "google") == {
        "folder_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz"
    }


@requires_db
def test_get_connection_config_returns_none_when_never_set(store, org_cleanup):
    org_id = store.create_organization("Source Config No Config Org")
    org_cleanup.append(org_id)

    save_connection(
        org_id,
        "google",
        OAuthTokens(
            access_token="goog_access",
            refresh_token=None,
            expires_at=None,
            external_workspace_id="drive-user@example.com",
        ),
    )

    assert get_connection_config(org_id, "google") is None


@requires_db
def test_set_connection_config_raises_when_no_connection(store, org_cleanup):
    org_id = store.create_organization("Source Config No Connection Org")
    org_cleanup.append(org_id)

    with pytest.raises(ConfigurationError):
        set_connection_config(org_id, "google", {"folder_id": "abc"})


# -- Pure parser (no DB) ---------------------------------------------------------


def test_extract_drive_folder_id_from_full_url():
    url = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=sharing"
    assert extract_drive_folder_id(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz"


def test_extract_drive_folder_id_from_u_variant_with_query():
    url = "https://drive.google.com/drive/u/0/folders/1AbCdEfGhIjKlMnOpQrStUvWxYz?usp=drive_link"
    assert extract_drive_folder_id(url) == "1AbCdEfGhIjKlMnOpQrStUvWxYz"


def test_extract_drive_folder_id_from_bare_id():
    bare_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
    assert extract_drive_folder_id(bare_id) == bare_id


def test_extract_drive_folder_id_rejects_empty_string():
    with pytest.raises(ConfigurationError):
        extract_drive_folder_id("")


def test_extract_drive_folder_id_rejects_unrelated_google_doc_link():
    with pytest.raises(ConfigurationError):
        extract_drive_folder_id(
            "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUvWxYz/edit"
        )
