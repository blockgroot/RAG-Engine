"""Model-client limits from the production-readiness audit: no SDK retries,
a cap and a truncation flag on tool turns, every request paced."""

from __future__ import annotations

from types import SimpleNamespace

from app.llm import pacing
from app.llm.openai_provider import DEFAULT_TOOL_MAX_TOKENS, OpenAICompatProvider


def _response(finish: str, content: str = "ok"):
    message = SimpleNamespace(content=content, tool_calls=None,
                              model_dump=lambda: {"role": "assistant", "content": content})
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5),
        model="m",
    )


def _provider(finish: str, sent: list):
    p = OpenAICompatProvider(model="m", api_key="k", base_url="http://127.0.0.1:9")
    p._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: sent.append(kw) or _response(finish))))
    return p


def test_sdk_retries_are_off_by_default():
    """A 429 is the quota: the SDK must not wait it out on a shared thread."""
    p = OpenAICompatProvider(model="m", api_key="k", base_url="http://127.0.0.1:9")
    assert p._client.max_retries == 0


def test_tool_turn_is_capped_and_a_cut_reply_is_flagged():
    sent: list = []
    p = _provider("length", sent)
    p.generate_with_tools([{"role": "user", "content": "hi"}])
    assert sent[0]["max_tokens"] == DEFAULT_TOOL_MAX_TOKENS
    assert p.last_truncated is True

    sent.clear()
    p = _provider("stop", sent)
    p.generate_with_tools([{"role": "user", "content": "hi"}], max_tokens=50)
    assert sent[0]["max_tokens"] == 50
    assert p.last_truncated is False


def test_every_request_is_paced_without_log_llm_call():
    pacing.reset_for_tests()
    p = _provider("stop", [])
    p.generate("hi")
    p.generate_with_tools([{"role": "user", "content": "hi"}])
    assert pacing.calls_in_window() == 2
