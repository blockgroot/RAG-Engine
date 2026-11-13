"""Security helpers; the package exports credential encryption at rest.

Public API:
    from app.security import encrypt, decrypt
    token = encrypt("ntn_secret")
    plaintext = decrypt(token)
"""

from .crypto import encrypt, decrypt

__all__ = ["encrypt", "decrypt"]
