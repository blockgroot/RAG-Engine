"""Single construction point for the injection guard."""

from __future__ import annotations

from ..config.settings import GroqSettings, GuardSettings
from .base import InjectionGuard
from .prompt_guard import GroqPromptGuard


def build_injection_guard(settings: GuardSettings | None = None) -> InjectionGuard | None:
    """The configured guard, or ``None`` when ``GUARD_MODE=off`` or Groq has no key.

    ``None`` is the "not scoring" state every caller already handles, so an
    unconfigured deploy is byte-identical to before the guard existed.
    """
    settings = settings or GuardSettings.from_env()
    groq = GroqSettings.from_env()
    if not settings.enabled or not groq.api_key:
        return None
    return GroqPromptGuard(
        api_key=groq.api_key, base_url=groq.base_url, model=settings.model, timeout=settings.timeout
    )
