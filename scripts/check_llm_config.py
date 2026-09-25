"""Boot-time LLM config check: refuse to start on a config we can PROVE is wrong.

Run by ``docker-entrypoint.sh`` before the server starts, so a bad model
config fails the DEPLOY — Render keeps the previous container serving — instead
of failing every answer after it went live.

``LLM_CONFIG_CHECK`` (``app/config/settings.py``):
    strict (default)  exit 1 on an unknown adapter, a conflicting base URL, a
                      model the provider's own list does not contain, or a key
                      the provider rejects outright (401)
    warn              print the same findings, always exit 0
    off               skip

Never fatal: an unreachable model list (network, a provider with no
``/models``) and an unset ``LLM_ADAPTER`` (legacy config — works as before,
warned). A deploy must not fail because a vendor was briefly down.

Run by hand:
    python scripts/check_llm_config.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.config.settings import LLMSettings
from app.core.exceptions import ConfigurationError
from app.llm import adapters
from app.llm.factory import aux_endpoint, main_endpoint


def check(settings: LLMSettings, *, client_for=None) -> list[str]:
    """Problems that prove the config wrong. Warnings are printed, not returned."""
    problems: list[str] = []
    if not settings.adapter:
        print(
            "LLM check: WARNING LLM_ADAPTER is not set; using LLM_BASE_URL as-is. "
            f"Set it to one of: {', '.join(sorted(adapters.ADAPTERS))}"
        )
    for label, resolve in (("LLM", main_endpoint), ("LLM_AUX", aux_endpoint)):
        if label == "LLM_AUX" and not (settings.aux_model or settings.aux_has_own_endpoint):
            continue  # identical to the main model, already checked
        try:
            endpoint = resolve(settings)
        except ConfigurationError as exc:
            problems.append(f"{label}: {exc}")
            continue
        client = client_for(endpoint) if client_for else None
        # Capped: the server is not listening yet, so a hung provider must not
        # hold the boot for the full answer timeout.
        result = adapters.verify_model(endpoint, timeout=min(settings.timeout, 10.0), client=client)
        where = endpoint.adapter or endpoint.base_url or "default"
        if result.status == adapters.OK:
            print(f"LLM check: {label} model {endpoint.model!r} on {where}: ok")
        elif result.status == adapters.UNVERIFIED:
            print(f"LLM check: WARNING {label} model {endpoint.model!r} on {where} "
                  f"not verified ({result.detail})")
        elif endpoint.adapter is None:
            # Legacy config: the model list is advisory until the operator has
            # named an adapter, so an unmigrated prod deploy is never blocked.
            print(f"LLM check: WARNING {label}: {result.detail}")
        else:
            problems.append(f"{label}: {result.detail}")
    return problems


def main() -> int:
    load_dotenv()
    settings = LLMSettings.from_env()
    if settings.config_check == "off":
        print("LLM check: skipped (LLM_CONFIG_CHECK=off)")
        return 0
    problems = check(settings)
    for problem in problems:
        print(f"LLM check: FAILED {problem}")
    if problems and settings.config_check == "strict":
        print("LLM check: refusing to start. Fix the config, or set LLM_CONFIG_CHECK=warn.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
