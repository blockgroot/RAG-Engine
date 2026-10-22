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
_WORD = re.compile(r"\w+")
_MENTION = re.compile(r"(?<![\w@])@([A-Za-z0-9][\w.-]*)")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _text(value: object) -> str:
    """The model's value as text; anything that is not a string is empty."""
    return value if isinstance(value, str) else ""


def _fold(text: object) -> str:
    return _SPACE.sub(" ", _text(text).casefold()).strip()


def asked_in(phrase: str | None, question: str) -> bool:
    """True when ``phrase`` appears in the asker's words (case and spacing aside)."""
    folded = _fold(phrase or "")
    return bool(folded) and folded in _fold(question)


def asked_for(quote: str | None, question: str) -> bool:
    """True when the words that ask for an action are the asker's: every word
    of ``quote`` is in ``question``, in order, words left out between them.
    "Create a Linear issue in BEN and a GitHub issue in acme/app" asks for the
    GitHub issue in no one phrase ("Create" is shared), so an exact quote of
    that ask could not exist. A target is still matched exactly (``asked_in``)."""
    said = iter(_WORD.findall(_fold(question)))
    words = _WORD.findall(_fold(quote or ""))
    return bool(words) and all(w in said for w in words)


def require_named(name: str | None, question: str, what: str) -> str:
    """The model's name for a target, accepted only if the asker wrote it."""
    if not asked_in(name, question):
        raise ActionRejected(
            f"Name the {what} in your request, so I know you chose it."
        )
    return _text(name).strip()


def clean_title(text: str | None) -> str:
    title = _SPACE.sub(" ", _CONTROL.sub("", _text(text))).strip()
    if not title:
        raise ActionRejected("The draft has no title.")
    if len(title) > TITLE_LIMIT:
        title = title[: TITLE_LIMIT - 1].rstrip() + "…"
    return title


def clean_body(text: str | None, question: str, source_uris: list[str] | tuple[str, ...]) -> str:
    """The body with only the asker's links and mentions left, cut visibly."""
    body = _CONTROL.sub("", _text(text)).strip()
    body = enforce_link_provenance(body, [question, *source_uris], allowlist=())
    asked = _fold(question)

    def mention(match: re.Match[str]) -> str:
        whole = match.group(0)
        return whole if whole.casefold() in asked else match.group(1)

    body = _MENTION.sub(mention, body)
    if len(body) > BODY_LIMIT:
        body = body[: BODY_LIMIT - len(TRIMMED)].rstrip() + TRIMMED
    return body


def without_title_heading(body: str, title: str) -> str:
    """Drop a first heading that only repeats the page's title: Notion shows
    the title already, and the model often writes it again."""
    first, _, rest = body.lstrip().partition("\n")
    if re.sub(r"^#+\s*", "", first).strip().casefold() == title.strip().casefold() and first.startswith("#"):
        return rest.lstrip("\n")
    return body
