"""Phase 10: credential encryption at rest (app/security/crypto.py)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.config.settings import AuthSettings
from app.core.exceptions import EncryptionError
from app.security import decrypt, encrypt


def _settings(*keys: str) -> AuthSettings:
    return AuthSettings(encryption_keys=list(keys))


def test_encrypt_decrypt_round_trip():
    key = Fernet.generate_key().decode()
    settings = _settings(key)

    ciphertext = encrypt("ntn_supersecret", settings=settings)

    assert ciphertext != "ntn_supersecret"
    assert decrypt(ciphertext, settings=settings) == "ntn_supersecret"


def test_decrypt_tries_every_configured_key_for_rotation():
    old_key = Fernet.generate_key().decode()
    new_key = Fernet.generate_key().decode()

    # Encrypted while `old_key` was the only (active) key.
    ciphertext = encrypt("ntn_oldsecret", settings=_settings(old_key))

    # After rotation, `new_key` is active (first) but `old_key` must still decrypt.
    rotated = _settings(new_key, old_key)
    assert decrypt(ciphertext, settings=rotated) == "ntn_oldsecret"

    # And new encryptions use the new (first) key.
    new_ciphertext = encrypt("ntn_newsecret", settings=rotated)
    assert decrypt(new_ciphertext, settings=_settings(new_key)) == "ntn_newsecret"


def test_decrypt_fails_when_no_configured_key_matches():
    right_key = Fernet.generate_key().decode()
    wrong_key = Fernet.generate_key().decode()
    ciphertext = encrypt("ntn_secret", settings=_settings(right_key))

    with pytest.raises(EncryptionError):
        decrypt(ciphertext, settings=_settings(wrong_key))


def test_encrypt_without_configured_keys_raises_configuration_style_error():
    with pytest.raises(EncryptionError):
        encrypt("ntn_secret", settings=_settings())


def test_invalid_key_format_raises_encryption_error():
    with pytest.raises(EncryptionError):
        encrypt("ntn_secret", settings=_settings("not-a-valid-fernet-key"))
