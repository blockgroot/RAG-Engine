"""Handbook's checks on a drafted form before it becomes a card: what
``prepare`` refuses, blanks and links no one gave, whether the card holds
the content the request asked for, and which channels the loop has read.
The model judges meaning; code checks what is checkable."""

from __future__ import annotations

import json
import logging
import re

from ..core.exceptions import ActionError, ConfigurationError, OAuthReauthRequiredError
from .base import ActionContext
from .draft import asked_in
from .prompts import SEARCH, _LATER, build_cover_prompt, build_parts_prompt

logger = logging.getLogger("actions")


def _json_list(text: str | None) -> list:
    start = (text or "").find("[")
    if start == -1:
        return []
    try:
        got, _ = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        return []
    return got if isinstance(got, list) else []


#: Parts of one request checked; past this the rest is not.
MAX_PARTS = 8


#: Each check's reply budget: a reasoning model spent 200 thinking and replied
#: with nothing, so the check never ran.
CHECK_TOKENS = 2000


def _completeness(llm, question: str):
    """``check(label, args)``: the request's parts a card lacks, so a weak
    model's half-done card ("DM Raj the #hb-testing pointers and the link"
    became a DM with neither) is sent back once and what still lacks is said,
    whatever the model. The parts are quotes of the request, kept only when
    they are in it; the judgement is the model's, every check of it is code's.
    A failed call checks nothing: it never blocks a card."""
    parts: list[str] | None = None
    #: Per part, as the model labelled it and code kept it: the request's words
    #: naming where it is written, and the items it spells out to copy.
    where: dict[str, str] = {}
    values: dict[str, list[str]] = {}

    def content() -> list[str]:
        """The request's content parts, read once: quotes of the request."""
        nonlocal parts
        if parts is None:
            try:
                said = _json_list(llm.generate(build_parts_prompt(question), max_tokens=CHECK_TOKENS))
            except Exception:  # noqa: BLE001 - a check that fails never blocks the card
                logger.warning("Act: the completeness check failed", exc_info=True)
                said = []
            quotes = (str(p.get("quote") or "").strip() for p in said
                      if isinstance(p, dict) and p.get("kind") == "content")
            parts = list(dict.fromkeys(q for q in quotes if q and asked_in(q, question)))[:MAX_PARTS]
            for p in said:
                q = str(p.get("quote") or "").strip() if isinstance(p, dict) else ""
                if q not in parts:
                    continue
                said_in = str(p.get("in") or "").strip()
                if said_in and asked_in(said_in, question):
                    where[q] = said_in.casefold()
                items = [str(v).strip() for v in p.get("values") or [] if str(v).strip()]
                if items and all(asked_in(v, question) for v in items):
                    values[q] = items
        return parts

    def belongs(part: str, label: str, args: dict) -> bool:
        """Whether the part is written into this card: its "in" words name the
        card's tool or one of its values. Unlabelled, every card is judged."""
        said_in = where.get(part)
        if not said_in:
            return True
        named = [label.split(":")[0], *(v for v in args.values() if isinstance(v, str) and 0 < len(v) <= 200)]
        # Whole words: a sheet titled "T" is not named by "pos(t) in #eng".
        return any(re.search(rf"(?<!\w){re.escape(n.casefold().lstrip('#'))}(?!\w)", said_in)
                   for n in named if n.strip().lstrip("#"))

    def check(label: str, args: dict) -> list[str]:
        nonlocal parts
        try:
            content()
            if not parts:
                return []               # the request asks for no content of its own
            # Content is judged in the card's body, never its title: Gemma took
            # "Summary of #hb-testing discussion" for the summary, and the card
            # had no description at all.
            from .mcp import _TITLE

            form = json.dumps({k: v for k, v in args.items() if k not in OWN_FIELDS and not _TITLE.search(k)},
                              ensure_ascii=False)
            # What code can check, it does: a part for another card ("post that
            # the tracker is ready" is not the sheet's) is not judged here, and
            # a part whose items are all on the card is there. GPT-OSS judged a
            # sheet with every column and row as lacking them, and asked the
            # Slack post for them, which then repeated the sheet.
            sent = json.dumps(args, ensure_ascii=False).casefold()
            judged = [p for p in parts if belongs(p, label, args)
                      and not (values.get(p) and all(v.casefold() in sent for v in values[p]))]
            if not judged:
                return []
            gone = _json_list(llm.generate(build_cover_prompt(judged, label, form, question),
                                           max_tokens=CHECK_TOKENS))
        except Exception:  # noqa: BLE001 - a check that fails never blocks the card
            logger.warning("Act: the completeness check failed", exc_info=True)
            parts = parts or []
            return []
        return [judged[n - 1] for n in dict.fromkeys(gone) if isinstance(n, int) and 0 < n <= len(judged)]

    check.content = content
    return check


_CHANNEL = re.compile(r"(?<![\w#])#([a-z0-9][a-z0-9._-]{0,79})", re.IGNORECASE)


def _channels_read(messages: list[dict]) -> set[str]:
    """The channels a read in an EARLIER turn covered (casefolded): a draft
    written in the same turn as its read never saw what the read returned,
    and a refused read read nothing."""
    name = SEARCH["function"]["name"]
    answers = {m.get("tool_call_id"): str(m.get("content") or "") for m in messages if m.get("role") == "tool"}
    seen: set[str] = set()
    now = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=len(messages))
    for m in messages[:now]:
        for c in m.get("tool_calls") or []:
            if c["function"]["name"] != name or answers.get(c["id"], "").startswith("Refused"):
                continue
            try:
                given = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                continue
            given = given if isinstance(given, dict) else {}
            seen.add(str(given.get("slack_channel") or "").lstrip("#").casefold())
            seen |= {x.casefold() for x in _CHANNEL.findall(str(given.get("content_words") or ""))}
    return seen


def _found_nothing(text: str) -> bool:
    """A lookup's answer that is an empty list, or an object whose lists are
    all empty (``{"issues": []}``, ``{"total_count": 0, "items": []}``). By
    shape only: a tool that answers in words is never judged."""
    # The first place JSON parses: an answer is labelled ("[Linear: List issues] {…}").
    body, decoder = None, json.JSONDecoder()
    for start in (i for i, c in enumerate(text) if c in "{["):
        try:
            body, _ = decoder.raw_decode(text, start)
            break
        except json.JSONDecodeError:
            continue
    else:
        return False
    if isinstance(body, list):
        return not body
    lists = [v for v in body.values() if isinstance(v, list)] if isinstance(body, dict) else []
    return bool(lists) and not any(lists)


_LINK = re.compile(r"https?://[^\s\"'<>)\]]+")


#: A template slot inside drafted text: format, not wording ("{{result_of_card_1}}").
_PLACEHOLDER = re.compile(r"\{\{[^{}]{1,80}\}\}")


#: A slot for the person to fill in ("Best, [Your Name]"): format, not wording.
#: A markdown link's "[text](" is not one.
_BLANK = re.compile(r"\[[A-Z][A-Za-z ]{1,80}\](?!\()")


def _precheck(action, args: dict, question: str, *, org_id: str, user_id: str, email: str,
              found: list[str], sources: tuple = (), prepared: dict | None = None) -> str | None:
    """Handbook's own checks on the form (``prepare``), before it is a card,
    so a value the model must look up or a target that does not exist is said
    back to it. Nothing is stored; ``_draft`` checks again, authoritatively.

    A link the draft would lose (no one gave it: not the asker, a source, or
    an approved card) is said too, rather than becoming "[link removed]" on
    the card: models wrote ".../issues/???" for an issue not yet made."""
    try:
        token = action.token_for(org_id, user_id)
        ctx = ActionContext(org_id, None, user_id, email, question, token, tuple(sources), None, None,
                            "\n".join(found))
        proposal = action.prepare(args, ctx)
    except (ConfigurationError, OAuthReauthRequiredError):
        return None                       # said by ``_draft``, in the person's terms
    except ActionError as exc:
        return str(exc)
    sent = json.dumps({k: v for k, v in args.items() if k not in OWN_FIELDS}, ensure_ascii=False)
    blank = _PLACEHOLDER.search(sent)
    if blank:
        # A value the model meant to fill later, written into the text: the
        # card would say "{{result_of_card_1}}" where the link should be.
        return f"the text has a placeholder ({blank.group(0)[:60]}) where an earlier card's value should be. {_LATER}"
    slot = _BLANK.search(sent)
    if slot:
        # GPT-OSS signed a DM "Best, [Your Name]"; it is sent as the person.
        return (f"the text has a blank for someone to fill in ({slot.group(0)}). Write it without "
                "blanks or a sign-off: it is sent as the user, under their name")
    if prepared is not None:
        prepared["args"] = (proposal.payload or {}).get("args")
    kept = json.dumps(proposal.payload, ensure_ascii=False)
    lost = [u for u in _LINK.findall(sent) if u.rstrip(".,;") not in kept]
    if lost:
        return (f"the link {lost[0]} is not one the user, a source or an approved card gave, so it would "
                f"be removed. If it is the link of a card not approved yet: {_LATER}")
    return None


#: Handbook's own field on a write's form; never sent to the tool.
OWN_FIELDS = ("request_words",)
