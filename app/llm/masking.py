"""Mask personal identifiers in every prompt; restore them in every reply.

**Why here.** Every model call in the app — answers, rewrites, audits, live
reads, charts, Agent drafts, ingest contextualization — goes through one of
the two factory-built providers, so wrapping them covers all of it without
touching a single caller. The reply is restored before it leaves this wrapper,
so citations, link provenance, caching, storage and Slack see real values
exactly as before.

**Tool calls.** Arguments are restored before the caller sees them: a GitHub
lookup or a draft needs the real value, never a placeholder.

**Failure.** If the text cannot be checked, the call fails as an
``LLMProviderError`` — every caller already handles that — and nothing is sent.
"""

from __future__ import annotations

from ..core.exceptions import LLMProviderError, PrivacyError
from ..privacy import IdentifierDetector, Vault
from .base import ChatResult, LLMProvider, ToolCall

#: Only sent when something was masked, so an ordinary prompt is unchanged.
PLACEHOLDER_NOTE = (
    "Some values in this text are replaced by placeholders such as <PHONE_1>. "
    "When you refer to one, copy the placeholder exactly as written.\n\n"
)


class MaskingLLMProvider(LLMProvider):
    def __init__(self, inner: LLMProvider, detector: IdentifierDetector | None) -> None:
        self._inner = inner
        self._detector = detector

    def __getattr__(self, name: str):
        # model, last_usage, last_truncated, configured_backends, ... are the
        # wrapped provider's; metering and the model picker read them.
        if name == "_inner":  # not yet set (copy/pickle): no recursion
            raise AttributeError(name)
        return getattr(self._inner, name)

    def generate(self, prompt: str, *, max_tokens: int | None = None) -> str:
        vault = Vault(self._detector)
        masked = _masked(vault, prompt)
        if vault.used:
            masked = PLACEHOLDER_NOTE + masked
        return vault.restore(self._inner.generate(masked, max_tokens=max_tokens))

    def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str | None = None,
        timeout: float | None = None,
    ) -> ChatResult:
        vault = Vault(self._detector)
        try:
            sent = [_mask_message(vault, m) for m in messages]
        except PrivacyError as exc:
            raise LLMProviderError(str(exc), cause=exc) from exc
        if vault.used and sent and isinstance(sent[0].get("content"), str):
            sent[0] = {**sent[0], "content": PLACEHOLDER_NOTE + sent[0]["content"]}
        result = self._inner.generate_with_tools(
            sent, tools=tools, tool_choice=tool_choice, timeout=timeout
        )
        return ChatResult(
            text=vault.restore(result.text),
            tool_calls=[
                ToolCall(id=c.id, name=c.name, arguments=vault.restore(c.arguments))
                for c in result.tool_calls
            ],
            raw_message=_restore_obj(vault, result.raw_message),
        )


def _masked(vault: Vault, text: str) -> str:
    try:
        return vault.mask(text)
    except PrivacyError as exc:
        raise LLMProviderError(str(exc), cause=exc) from exc


def _mask_message(vault: Vault, message: dict) -> dict:
    """Mask what a model reads in a message: its content (plain or parts) and
    the arguments of tool calls replayed from an earlier turn."""
    out = dict(message)
    content = out.get("content")
    if isinstance(content, str):
        out["content"] = vault.mask(content)
    elif isinstance(content, list):
        out["content"] = [
            {**p, "text": vault.mask(p["text"])}
            if isinstance(p, dict) and isinstance(p.get("text"), str) else p
            for p in content
        ]
    if isinstance(out.get("tool_calls"), list):
        out["tool_calls"] = [_mask_tool_call(vault, c) for c in out["tool_calls"]]
    return out


def _mask_tool_call(vault: Vault, call):
    if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
        return call
    fn = call["function"]
    if not isinstance(fn.get("arguments"), str):
        return call
    return {**call, "function": {**fn, "arguments": vault.mask(fn["arguments"])}}


def _restore_obj(vault: Vault, value):
    if isinstance(value, str):
        return vault.restore(value)
    if isinstance(value, dict):
        return {k: _restore_obj(vault, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore_obj(vault, v) for v in value]
    return value
