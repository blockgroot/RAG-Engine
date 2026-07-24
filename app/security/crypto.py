"""Symmetric encryption for credentials stored at rest (Phase 10).

Used to protect ``oauth_connections.access_token_encrypted`` /
``refresh_token_encrypted`` — OAuth tokens must never be stored in plaintext.

Why ``MultiFernet`` over a single static key: a single key has no rotation
story (rotating it would make every existing row undecryptable in one shot).
``MultiFernet`` encrypts with the *first* key in ``AuthSettings.encryption_keys``
but can decrypt with *any* key in the list, so rotating is just prepending a
new key and re-encrypting rows at leisure, not a flag-day cutover. This stays
dependency-light and self-hostable (no external KMS) per CLAUDE.md §1 — a
cloud-KMS envelope-encryption backend is a valid future swap behind these same
two functions, not a requirement today.
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
    """Encrypt ``plaintext`` with the active (first) configured key.

    Returns a token safe to store as TEXT.
    """
    settings = settings or AuthSettings.from_env()
    fernet = _build_multi_fernet(settings)
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str, *, settings: AuthSettings | None = None) -> str:
    """Decrypt ``ciphertext``, trying every configured key (newest first).

    Raises ``EncryptionError`` if no configured key can decrypt it (wrong key,
    corrupted value, or rotated out with no matching key left).
    """
    settings = settings or AuthSettings.from_env()
    fernet = _build_multi_fernet(settings)
    try:
        return fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise EncryptionError(
            "Could not decrypt value with any configured AUTH_ENCRYPTION_KEYS",
            cause=exc,
        ) from exc
