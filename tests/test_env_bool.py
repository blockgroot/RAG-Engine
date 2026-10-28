"""An empty switch is unset, not false.

The self-hosted .env lists optional switches with no value; an empty
RETRIEVAL_RERANK_ENABLED= once read as "false" would turn ranking off.
"""

from app.config.settings import env_bool


def test_empty_means_default(monkeypatch):
    monkeypatch.setenv("HB_TEST_SWITCH", "  ")
    assert env_bool("HB_TEST_SWITCH", True) is True
    monkeypatch.setenv("HB_TEST_SWITCH", "false")
    assert env_bool("HB_TEST_SWITCH", True) is False
