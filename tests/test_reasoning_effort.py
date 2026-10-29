"""LLM_REASONING_EFFORT reaches the configured model's requests.

A reasoning model (GPT-OSS on Ollama Cloud) spent a 200-token question check
thinking and returned an empty message; "low" is what the model catalogue
already sends it. The extra may be dropped if an endpoint refuses it.
"""

from app.llm import adapters


def _provider(effort):
    endpoint = adapters.resolve(
        "custom", base_url="https://ollama.com/v1", model="gpt-oss:120b", api_key="k"
    )
    return adapters.build_provider(endpoint, timeout=5, reasoning_effort=effort)


def test_effort_is_sent_and_droppable():
    provider = _provider("low")
    assert provider._extra_body == {"reasoning_effort": "low"}
    assert provider._extra_body_optional is True


def test_nothing_is_sent_without_it():
    assert _provider(None)._extra_body is None
