"""Symmetric encryption for credentials stored at rest (OAuth tokens, keys).

``MultiFernet`` encrypts with the first key in
``AuthSettings.encryption_keys`` and decrypts with any, so rotation is
prepending a key, not a flag day. No external KMS, to stay self-hostable.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config.settings import AuthSettings
from app.core.exceptions import EncryptionError


def _build_multi_fernet(settings: AuthSettings) -> MultiFernet:
    if not settings.encryption_keys:
        raise EncryptionError(
            "No encryption keys configured — set AUTH_ENCRYPTION_KEYS to one or "
            "more Fernet keys (generate with "
            "`python -c \"from cryptography.fernet import Fernet; "
            'print(Fernet.generate_key().decode())"`)'
        )
    try:
        return MultiFernet([Fernet(key.encode()) for key in settings.encryption_keys])
    except (ValueError, TypeError) as exc:
        raise EncryptionError("Invalid AUTH_ENCRYPTION_KEYS value", cause=exc) from exc


def encrypt(plaintext: str, *, settings: AuthSettings | None = None) -> str:
    """Encrypt ``plaintext`` with the active (first) key; safe to store as TEXT."""
    settings = settings or AuthSettings.from_env()
    fernet = _build_multi_fernet(settings)
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str, *, settings: AuthSettings | None = None) -> str:
    """Decrypt ``ciphertext`` with any configured key. Raises
    ``EncryptionError`` when none can."""
    settings = settings or AuthSettings.from_env()
    fernet = _build_multi_fernet(settings)
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise EncryptionError(
            "Could not decrypt value with any configured AUTH_ENCRYPTION_KEYS",
            cause=exc,
        ) from exc
