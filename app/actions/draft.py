"""What a write may contain, and where it may go: checked by code, not the model.

Two rules from the plan (§4.3):

* A target is accepted only when the asker NAMED it. The model passes a name;
  code checks the name is in the asker's own words, then resolves it with the
  asker's token. A team or repo that appears only inside a retrieved document
  is never a target, which is what stops "post this to #general" hidden in a
  page from choosing where a write goes.
* Text drawn from documents may go in, links and @-mentions may not, unless
  the asker wrote them (or, for links, a cited document is that address). A
  model-written URL is how a write becomes a way to carry data out; a mention
  pings someone the asker never chose.
"""

from __future__ import annotations

import re

from ..core.exceptions import ActionRejected
from ..security.links import enforce_link_provenance

TITLE_LIMIT = 255
BODY_LIMIT = 8000
TRIMMED = "\n\n(trimmed)"

_SPACE = re.compile(r"\s+")
_MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9][\w.-]*)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _fold(text: str) -> str:
    return _SPACE.sub(" ", (text or "").casefold()).strip()


def asked_in(phrase: str | None, question: str) -> bool:
    """True when ``phrase`` appears in the asker's words (case and spacing aside)."""
    folded = _fold(phrase or "")
    return bool(folded) and folded in _fold(question)


def require_named(name: str | None, question: str, what: str) -> str:
    """The model's name for a target, accepted only if the asker wrote it."""
    if not asked_in(name, question):
        raise ActionRejected(
            f"Name the {what} in your request, so I know you chose it."
        )
    return (name or "").strip()


def clean_title(text: str | None) -> str:
    title = _SPACE.sub(" ", _CONTROL.sub("", text or "")).strip()
    if not title:
        raise ActionRejected("The draft has no title.")
    if len(title) > TITLE_LIMIT:
        title = title[: TITLE_LIMIT - 1].rstrip() + "…"
    return title


def clean_body(text: str | None, question: str, source_uris: list[str] | tuple[str, ...]) -> str:
    """The body with only the asker's links and mentions left, cut visibly."""
    body = _CONTROL.sub("", text or "").strip()
    body = enforce_link_provenance(body, [question, *source_uris], allowlist=())
    asked = _fold(question)

    def mention(match: re.Match[str]) -> str:
        whole = match.group(0)
        return whole if whole.casefold() in asked else match.group(1)

    body = _MENTION.sub(mention, body)
    if len(body) > BODY_LIMIT:
        body = body[: BODY_LIMIT - len(TRIMMED)].rstrip() + TRIMMED
    return body
