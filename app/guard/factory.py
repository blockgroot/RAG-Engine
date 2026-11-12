"""Single construction point for the injection guard."""

from __future__ import annotations

from ..config.settings import GuardSettings
from .base import InjectionGuard
from .prompt_guard import GroqPromptGuard
from .safeguard import GroqSafeguard


def build_injection_guard(settings: GuardSettings | None = None) -> InjectionGuard | None:
    """The configured guard, or ``None`` when ``GUARD_MODE=off`` or it has no key.

    ``None`` is the "not scoring" state every caller already handles, so an
    unconfigured deploy is byte-identical to before the guard existed.
    """
    settings = settings or GuardSettings.from_env()
    if not settings.enabled or not settings.api_key:
        return None
    if settings.backend == "safeguard":
        return GroqSafeguard(api_key=settings.api_key, base_url=settings.base_url,
                             timeout=settings.timeout, model=settings.safeguard_model)
    return GroqPromptGuard(
        api_key=settings.api_key, base_url=settings.base_url, model=settings.model,
        timeout=settings.timeout,
    )
