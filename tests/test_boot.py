"""scripts/boot.py: a wake-up skips the schema and checks only when nothing they depend on changed."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _boot():
    sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location("boot_under_test", _SCRIPTS / "boot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_same_settings_give_the_same_key_and_a_changed_model_does_not(monkeypatch):
    boot = _boot()
    monkeypatch.setenv("LLM_MODEL", "claude-haiku-5-5")
    first = boot.boot_key()
    assert boot.boot_key() == first, "a wake-up must find its own mark"
    monkeypatch.setenv("LLM_MODEL", "claude-sonnet-5-5")
    assert boot.boot_key() != first, "a new model must run the checks again"


def test_the_key_never_holds_a_setting(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-secret-value-123")
    key = _boot().boot_key()
    assert key.startswith("boot:") and "secret" not in key and len(key) == len("boot:") + 32
