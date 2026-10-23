"""Single construction point for the identifier detector."""

from __future__ import annotations

from ..config.settings import PrivacySettings
from .base import IdentifierDetector
from .patterns import PatternDetector


def build_detector(settings: PrivacySettings | None = None) -> IdentifierDetector | None:
    """The detector, or ``None`` when ``MASK_IDENTIFIERS`` is off.

    ``None`` makes every vault a pass-through, so switching masking off is
    byte-identical to before it existed.
    """
    settings = settings or PrivacySettings.from_env()
    if not settings.mask_identifiers:
        return None
    return PatternDetector()
