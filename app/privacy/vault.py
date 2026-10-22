"""Placeholders for one outbound call, and putting the real values back.

A vault lives for ONE call to an outside service and is then dropped. Nothing
is stored, so there is no table of real values to leak, and nothing can cross
from one company's call into another's. Each LLM reply is restored before it
leaves the wrapper, so every later call sees real values again and masks them
afresh.
"""

from __future__ import annotations

import re

from ..core.exceptions import PrivacyError
from .base import IdentifierDetector

KINDS = ("AADHAAR", "VID", "PAN", "CARD", "PHONE", "IFSC", "UPI")
_PLACEHOLDER = re.compile(r"<(?:" + "|".join(KINDS) + r")_\d{1,4}>")


class Vault:
    def __init__(self, detector: IdentifierDetector | None) -> None:
        self._detector = detector
        self._token_for: dict[tuple[str, str], str] = {}
        self._value_of: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    @property
    def used(self) -> bool:
        return bool(self._value_of)

    def mask(self, text: str) -> str:
        """``text`` with every identifier replaced by ``<KIND_n>``.

        Raises ``PrivacyError`` when detection itself fails: the caller must
        not send the text unmasked.
        """
        if not text or self._detector is None:
            return text
        try:
            found = self._detector.find(text)
        except Exception as exc:  # any detector fault blocks the send
            raise PrivacyError("Could not check text for personal identifiers", cause=exc) from exc
        if not found:
            return text
        parts: list[str] = []
        pos = 0
        for f in found:
            parts.append(text[pos:f.start])
            parts.append(self._token(f.kind, f.key, text[f.start:f.end]))
            pos = f.end
        parts.append(text[pos:])
        return "".join(parts)

    def restore(self, text: str | None) -> str | None:
        """Put back only placeholders THIS vault issued, matched exactly.

        One the model invented (``<PHONE_7>`` when only one was issued) is
        removed rather than guessed at. The bare form (``PHONE_1``) is accepted
        because models sometimes drop the brackets; it is restored only when
        this vault issued it.
        """
        if not text or self._detector is None:
            return text
        text = _PLACEHOLDER.sub(lambda m: self._value_of.get(m.group(0), ""), text)
        for token, value in self._value_of.items():
            bare = re.compile(r"(?<![\w<])" + re.escape(token[1:-1]) + r"(?![\w>])")
            text = bare.sub(lambda _m, v=value: v, text)
        return text

    def _token(self, kind: str, key: str, original: str) -> str:
        token = self._token_for.get((kind, key))
        if token is None:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            token = f"<{kind}_{self._counts[kind]}>"
            self._token_for[(kind, key)] = token
            self._value_of[token] = original
        return token


def redact(texts: list[str]) -> list[str]:
    """One-way masking for services whose output is not text we show
    (embeddings, reranking, the injection guard). One vault for the batch, so
    a number in the query and in a passage gets the same placeholder."""
    from .factory import build_detector

    vault = Vault(build_detector())
    return [vault.mask(t) for t in texts]
