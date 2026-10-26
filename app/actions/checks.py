"""Handbook's checks on a drafted form before it becomes a card: what
``prepare`` refuses, blanks and links no one gave, whether the card holds
the content the request asked for, and which channels the loop has read.
The model judges meaning; code checks what is checkable."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading

from ..core.exceptions import ActionError, ConfigurationError, OAuthReauthRequiredError
from .base import ActionContext
from .draft import asked_in
from .prompts import SEARCH, TOOL_FENCE, _LATER, build_cover_prompt, build_parts_prompt

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
    #: The request's action parts ("Add", "share"), quotes of its own words.
    doing: list[str] = []
    #: Per part, where its content comes from as the model said: "read",
    #: "lookup", "earlier" or "request".
    origin: dict[str, str] = {}
    once = threading.Lock()

    def content() -> list[str]:
        """The request's content parts, read once: quotes of the request."""
        with once:
            return _content()

    def _content() -> list[str]:
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
            doing[:] = list(dict.fromkeys(
                q for p in said if isinstance(p, dict) and p.get("kind") == "action"
                and (q := str(p.get("quote") or "").strip()) and asked_in(q, question)))[:MAX_PARTS]
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
                if p.get("from") in ("read", "lookup", "earlier", "request"):
                    origin[q] = p["from"]
        return parts

    def start() -> None:
        """Read the parts now, beside the loop's first turn, so the first
        card's check does not wait for them. The request's model choice is a
        ContextVar, so the thread runs in this request's context."""
        threading.Thread(target=contextvars.copy_context().run, args=(content,), daemon=True).start()

    def needs(label: str, args: dict) -> list[str]:
        """This card's content parts that must be read or looked up first."""
        return [p for p in content() if origin.get(p) in ("read", "lookup") and belongs(p, label, args)]

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
            # Only what is written counts: request_words quotes the request
            # ("…with the title, assignee and status of…"), so every item was
            # "on the card" and an empty sheet passed.
            sent = form.casefold()
            # Only content the request writes out ("rows A, B") is there when
            # its items are: content to read or look up is not. "The title,
            # assignee and status of the open issues in BEN" listed its column
            # names as items, and a sheet of just that header passed.
            judged = [p for p in parts if belongs(p, label, args)
                      and not (values.get(p) and origin.get(p) not in ("read", "lookup")
                               and all(v.casefold() in sent for v in values[p]))]
            # Content to read or look up, whose card lists hold only the
            # request's own words (a header), is missing: checked in code.
            cells = _list_cells(args)
            hollow = [p for p in judged if origin.get(p) in ("read", "lookup") and cells
                      and all(c in question.casefold() for c in cells)]
            judged = [p for p in judged if p not in hollow]
            # The mirror: lists holding more than the request's words hold what
            # was fetched (``_untraced`` refuses them otherwise). GPT-OSS judged
            # a sheet of every BEN issue as lacking "the title, assignee and
            # status of the open issues". With one such part, they are it.
            fetched = [p for p in judged if origin.get(p) in ("read", "lookup")]
            if len(fetched) == 1 and cells:
                judged.remove(fetched[0])
            if not judged:
                return hollow
            gone = _json_list(llm.generate(build_cover_prompt(judged, label, form, question),
                                           max_tokens=CHECK_TOKENS))
        except Exception:  # noqa: BLE001 - a check that fails never blocks the card
            logger.warning("Act: the completeness check failed", exc_info=True)
            parts = parts or []
            return []
        return hollow + [judged[n - 1] for n in dict.fromkeys(gone) if isinstance(n, int) and 0 < n <= len(judged)]

    def actions() -> list[str]:
        content()
        return doing

    check.content = content
    check.actions = actions
    check.start = start
    check.needs = needs
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


def _fetched(messages: list[dict], fetchers: set[str]) -> list[str]:
    """What reads and lookups in EARLIER turns returned: a draft written in
    the same turn as its fetch never saw the answer, and a refused read
    (answered in plain words, not fenced) fetched nothing."""
    answers = {m.get("tool_call_id"): str(m.get("content") or "") for m in messages if m.get("role") == "tool"}
    now = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=len(messages))
    return [answers[c["id"]] for m in messages[:now] for c in m.get("tool_calls") or []
            if c["function"]["name"] in fetchers and answers.get(c["id"], "").startswith(TOOL_FENCE)]


#: Fewer list items than this are not judged: one label is not a table.
MIN_TRACED = 3


def _list_cells(args: dict) -> list[str]:
    """The text items inside a card's lists (a Sheet's rows, a list of
    names), casefolded with spaces folded; never Handbook's own fields."""
    cells: list[str] = []

    def walk(value, in_list: bool) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k not in OWN_FIELDS:
                    walk(v, in_list)
        elif isinstance(value, list):
            for v in value:
                walk(v, True)
        elif in_list and isinstance(value, str) and len(value.strip()) >= 3 and re.search(r"[^\W\d_]", value):
            cells.append(" ".join(value.split()).casefold())

    walk(args, False)
    return cells


def _untraced(args: dict, texts: list[str], question: str = "") -> bool:
    """Whether most of the text items in a card's lists (a Sheet's rows, a
    list of names) are in nothing the request said or the loop fetched. A
    sheet "of the open issues in BEN" built with no lookup is made up,
    whatever its words; a date written in another format is one cell of
    many, so half is the line, not every cell."""
    # A header is the request's own words, not data: with it counted, a sheet
    # of one made-up issue under "Title, Assignee, Status" was half traced.
    said = " ".join(question.split()).casefold()
    cells = [c for c in _list_cells(args) if c not in said]
    if len(cells) < MIN_TRACED:
        return False
    seen = " ".join(" ".join(t.split()) for t in texts).casefold()
    return sum(c not in seen for c in cells) * 2 > len(cells)


def _cut_short(args: dict, texts: list[str]) -> list[str]:
    """List items the model ended with an ellipsis that nothing fetched or
    said has: its shorthand for "and the rest". Prod: a sheet of BEN's open
    issues stopped after three rows at "E3...". By format, not wording."""
    seen = " ".join(" ".join(t.split()) for t in texts).casefold()
    return [c for c in _list_cells(args) if re.search(r"(\.\.\.|…)$", c) and c not in seen]


def _found_nothing(text: str) -> bool:
    """A lookup's answer that is an empty list, or an object whose lists are
    all empty (``{"issues": []}``, ``{"total_count": 0, "items": []}``). By
    shape only: a tool that answers in words is never judged."""
    # The answer after its label ("[Linear: List issues]\n{…}"), never a piece
    # inside it: a long answer is cut at its bound, and the first part that
    # parsed was an issue's ``"labels": []``, so 50 issues read as none.
    label = re.match(r"\s*\[[^\]\n]*\]\s*(?=[{\[])", text)
    try:
        body, _ = json.JSONDecoder().raw_decode(text[label.end() if label else 0:].strip())
    except json.JSONDecodeError:
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
