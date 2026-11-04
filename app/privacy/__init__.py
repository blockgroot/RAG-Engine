"""Masking personal identifiers before text leaves for an outside service.

Public API:
    from app.privacy import Vault, build_detector, redact
    vault = Vault(build_detector())
    reply = vault.restore(llm.generate(vault.mask(prompt)))
"""

from .base import Found, IdentifierDetector
from .factory import build_detector
from .patterns import PatternDetector
from .vault import KINDS, Vault, redact

__all__ = [
    "Found",
    "IdentifierDetector",
    "KINDS",
    "PatternDetector",
    "Vault",
    "build_detector",
    "redact",
]
