"""The tool loop that drafts cards (``_agent_loop``): the model calls
tools until it replies in words. A lookup or a read runs at once; a write
pauses the loop as a card, and its call is answered once the person decides
it (``service.continue_run``)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

from ..config.settings import ActionsSettings
from ..core.exceptions import ConfigurationError, OAuthReauthRequiredError
from ..rag import progress
from ..security.links import enforce_link_provenance
from .base import Action
from .checks import _CHANNEL, OWN_FIELDS, _channels_read, _fetched, _found_nothing, _precheck, _untraced
from .draft import TYPOGRAPHY, asked_for, asked_in
from .prompts import _LATER, ALL_DONE, DECIDED, DONE_BEFORE, NOTHING_DRAFTED, NOTHING_FOUND, SEARCH, _fenced, build_act_prompt


#: Drafts one request may make ("an issue in Linear and in GitHub"). Each is
#: its own card, approved on its own; a cap keeps one sentence from fanning out.
MAX_ACTIONS = 5


#: The furthest back a read's ``days`` may reach.
MAX_READ_DAYS = 365


#: Model turns one request may take: reads, drafts and corrections included.
MAX_STEPS = 12


#: Turns in a row that draft nothing new and read nothing new before the loop
#: stops; the second is the model's chance to act on what it was told.
MAX_STALLS = 2


@dataclass
class _Drafting:
    """What one run of the loop drafted, and what it could not."""

    #: ``(action, args, tool_call_id)``: the calls the loop paused on.
    picked: list
    refused: list[str]
    left_out: list[str]
    reply: str | None = None
    turn: int = 0
    #: The turn each tool last drafted something new in.
    drafted_in: dict | None = None
    #: The conversation as it ended: the drafted calls are its last, unanswered.
    messages: list | None = None
    #: What the content reads returned, for the exposure check.
    sources: list | None = None
    #: Tools whose card was checked against the request's parts, and the
    #: parts each still lacks (``_completeness``).
    checked: set = field(default_factory=set)
    gaps: dict = field(default_factory=dict)
    #: Each drafted card's form as prepared (``_form_key``), so the same card
    #: asked again in other words is known after the person decides it.
    keys: list = field(default_factory=list)
    #: Per drafted call, what it does (``_did``): once its card succeeds, the
    #: same action is not drafted again in this request.
    did: dict = field(default_factory=dict)
    #: Per drafted call, its form keys: a card that FAILS stays draftable (the
    #: folder a Sheet goes in exists after it is made), so only decided ones block.
    forms: dict = field(default_factory=dict)


def _did(action, args: dict, request_words=None) -> str:
    """One action of the request: the tool, the words that asked for it and
    where it goes (every value but the drafted text). After "Post in
    #hb-testing the pointers from …" was approved, GPT-OSS posted the issue's
    link there too, quoting the same words: the same action, done twice."""
    from .mcp import _prose

    words = " ".join(str(request_words or args.get("request_words") or "").casefold().split())
    where = {k: v for k, v in args.items() if k not in OWN_FIELDS and v not in (None, "", [], {})
             and not (isinstance(v, str) and _prose(k))}
    return f"did:{action.name}:{words}:" + json.dumps(where, sort_keys=True, default=str)


def _form_key(action, args: dict) -> str:
    """The same form twice is one card, whatever words asked for it. An empty
    value is "not given", as ``prepare`` reads it: ``owner: ""`` and no owner
    drafted the same GitHub issue twice."""
    return action.name + json.dumps({k: v for k, v in args.items()
                                     if k not in OWN_FIELDS and v not in (None, "", [], {})}, sort_keys=True)


def _agent_loop(llm, question: str, tools: list[dict], offered: list[Action], *,
                org_id: str, user_id: str, email: str, found: list[str], looked: set[str],
                ran: dict, settings: ActionsSettings, in_thread: bool = False,
                messages: list[dict] | None = None, already: frozenset = frozenset(),
                sources: tuple = (), read=None, earlier: str = "", check=None) -> _Drafting:
    """A tool loop like any agent's (Onyx's): the model calls tools and is
    given each result in its own conversation, until it replies in words. A
    read runs and its answer comes back fenced. A write never runs here: the
    loop PAUSES at the turn that drafts one, leaving that call unanswered. It
    is answered with what really happened (the link, the tool's error, "the
    user cancelled") once the person decides the card, and the same
    conversation goes on (``continue_run``). So a later step sees the real
    link, and a refusal after approval comes back to be fixed, as any tool
    error does; nothing depends on the model announcing what comes next.

    ``read(words)`` reads company content as ``(sources, passages)``; with
    ``None`` none is offered. ``found`` keeps every lookup's answer (they
    ground a draft's ids); ``ran`` caches reads by their form."""
    messages = list(messages or [{"role": "user", "content": build_act_prompt(question, in_thread=in_thread,
                                                                               earlier=earlier)}])
    if read is not None:
        tools = [*tools, SEARCH]
    if hasattr(check, "start"):
        check.start()             # the request's parts, read beside the first turn
    by_name = {a.tool["function"]["name"]: a for a in offered}
    out = _Drafting([], [], [], drafted_in={}, sources=list(sources), messages=messages)
    drafted: dict[str, int] = {}
    refused: dict[str, str] = {}
    stalls, reminded = 0, False
    for step in range(MAX_STEPS):
        out.turn = step
        result = llm.generate_with_tools(messages, tools=tools, tool_choice="auto")
        calls = list(result.tool_calls or [])[:MAX_ACTIONS * 3] or _calls_in_text(result.text, tools)
        if not calls:
            if not out.picked and not reminded:
                # It SAID it drafted the change without calling the tool, or
                # gave up at once ("I can't access the Google Doc") without
                # trying the read tool: nothing would be shown. After an
                # approval it stopped with the GitHub half of "an issue in
                # Linear and in GitHub" never drafted. Either way one more
                # look, as Onyx reminds on its last cycle.
                reminded = True
                messages += [{"role": "assistant", "content": result.text or "(no reply)"},  # never empty
                             {"role": "user", "content": (ALL_DONE + _still_refused(messages, by_name)
                                                          + _not_asked_yet(messages, by_name, check))
                              if already else NOTHING_DRAFTED}]
                continue
            # A link survives as in any answer: only one the model was shown
            # (the request, a lookup, an approved card's result), never one it
            # wrote; "here is the link: [link removed]" helped nobody.
            shown = [question, *found, *(s.uri for s in out.sources if s.uri)]
            out.reply = enforce_link_provenance(result.text or "", shown, ()).strip() or None
            break
        ids = [getattr(c, "id", None) or f"call_{step}_{i}" for i, c in enumerate(calls)]
        # The OpenAI wire format every adapter speaks: content is null beside
        # tool calls (some providers refuse ""), and every call is answered:
        # now, or a drafted write once the person decides it. Whatever else a
        # provider attached to a call is sent back with it, unread: Gemini
        # refuses the next turn without its ``extra_content`` thought signature,
        # and a company's own model may need its own field the same way.
        extras = _call_extras(result.raw_message)
        messages.append({"role": "assistant", "content": result.text or None, "tool_calls": [
            {**extras.get(cid, {}), "id": cid, "type": "function",
             "function": {"name": c.name, "arguments": c.arguments or "{}"}}
            for cid, c in zip(ids, calls)]})
        progress_made = False
        # A card that makes what another card of this turn names (the folder a
        # Sheet goes in) is handled first, so the one that needs it waits.
        for cid, call in _makers_first(list(zip(ids, calls))):
            said, new = _one_call(call, by_name, question, org_id=org_id, user_id=user_id, email=email,
                                  found=found, looked=looked, ran=ran, settings=settings, out=out,
                                  drafted=drafted, refused=refused, already=already, read=read,
                                  call_id=cid, check=check)
            progress_made |= new
            if said is not None:
                messages.append({"role": "tool", "tool_call_id": cid, "content": said})
        if out.picked:
            break                 # paused: the drafted writes wait for the person
        stalls = 0 if progress_made else stalls + 1
        if stalls >= MAX_STALLS:
            break
    # A refusal the model fixed in a later turn (that tool drafted after it)
    # is not said; one it never fixed is.
    out.refused = [why for name, turn, why in refused.values() if out.drafted_in.get(name, -1) <= turn]
    out.messages = messages
    return out


def _not_asked_yet(messages: list[dict], by_name: dict, check) -> str:
    """The request's actions no card has asked for yet, named in the reminder:
    "Add … and share it with Raj as an editor" drafted the text, and after its
    approval GPT-OSS stopped; the share was never drafted. An action counts as
    done when some write's request_words quote it (code checks the quote)."""
    actions = getattr(check, "actions", None)
    if actions is None:
        return ""
    asked = []
    for m in messages:
        for c in m.get("tool_calls") or []:
            action = by_name.get(c["function"]["name"])
            if action is None or action.lookup:
                continue
            try:
                asked.append(str(json.loads(c["function"].get("arguments") or "{}").get("request_words") or ""))
            except (TypeError, ValueError):
                continue
    missing = [a for a in actions() if not any(asked_for(a, words) for words in asked)]
    return (f" The QUESTION also asks: {'; '.join(f'“{a}”' for a in missing)}, and no card has done it yet. "
            "If a tool you have does it, draft it now; if none does, say so." if missing else "")


def _names(args: dict) -> set[str]:
    """What a card names the thing it makes: its title or name."""
    from .mcp import _TITLE

    return {v.strip().casefold() for k, v in args.items()
            if isinstance(v, str) and _TITLE.search(k) and v.strip() and k not in OWN_FIELDS}


def _refs(args: dict) -> set[str]:
    """What a card points at: every value that is not drafted text."""
    from .mcp import _prose

    return {v.strip().casefold() for k, v in args.items()
            if isinstance(v, str) and not _prose(k) and v.strip() and k not in OWN_FIELDS}


def _makers_first(calls: list) -> list:
    """The turn's calls, a card whose title another card points at first."""
    def args(call):
        try:
            got = json.loads(call.arguments or "{}")
        except (TypeError, ValueError):
            return {}
        return got if isinstance(got, dict) else {}

    forms = [args(c) for _cid, c in calls]
    pointed = set().union(*(_refs(f) for f in forms)) if forms else set()
    return sorted(calls, key=lambda pair: not (_names(forms[calls.index(pair)]) & pointed))


def _still_refused(messages: list[dict], by_name: dict) -> str:
    """The tools whose card was refused and never drafted since, named in the
    reminder: a Slack post drafted before the GitHub issue existed was refused
    for its link, and after the issue was approved GPT-OSS said "links posted"
    without drafting it again."""
    names = {c["id"]: c["function"]["name"] for m in messages for c in m.get("tool_calls") or []}
    state: dict[str, bool] = {}
    for m in messages:
        name = names.get(m.get("tool_call_id")) if m.get("role") == "tool" else None
        if name in by_name and not by_name[name].lookup:
            # Refused before its card, or failed after approval (the folder a
            # Sheet goes in did not exist yet): either way not done.
            said = str(m.get("content") or "")
            state[name] = said.startswith("Refused") or "Not done: the user approved it, but" in said
    pending = [by_name[n].label for n, refused in state.items() if refused]
    return (f" Refused above and not drafted since: {'; '.join(pending)}. If the QUESTION asks for it, "
            "draft it now with the real values you have." if pending else "")


def _call_extras(raw_message) -> dict[str, dict]:
    """Per call id, the fields a provider attached to its tool call beyond the
    standard ``id``/``type``/``function``, so they go back exactly as received."""
    calls = (raw_message or {}).get("tool_calls") if isinstance(raw_message, dict) else None
    return {
        tc["id"]: {k: v for k, v in tc.items() if k not in ("id", "type", "function") and v is not None}
        for tc in calls or [] if isinstance(tc, dict) and tc.get("id")
    }


_NAME_KEYS = ("name", "tool", "tool_name", "function", "recipient_name")


_ARG_KEYS = ("arguments", "args", "parameters", "params", "toolInput", "tool_input", "tool_args", "input")


def _calls_in_text(text: str | None, tools: list[dict]) -> list:
    """A tool call the model WROTE instead of making, read as one (Onyx's
    fallback for the same habit): GPT-OSS replied with
    ``{"tool": "…save_issue", "toolInput": {…}}``, or with the bare arguments
    of the comment a chain needed, and nothing was drafted. A JSON object
    counts when it names an offered tool, or when its keys fit exactly one
    tool's form (every required field, nothing it does not take). It is then
    checked like any call."""
    if not text or "{" not in text:
        return []
    forms = {t["function"]["name"]: t["function"].get("parameters") or {} for t in tools}
    found, decoder, i = [], json.JSONDecoder(), text.find("{")
    while i != -1 and len(found) < MAX_ACTIONS:
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        i = text.find("{", end)
        if not isinstance(obj, dict):
            continue
        name = next((obj[k] for k in _NAME_KEYS if isinstance(obj.get(k), str) and obj[k] in forms), None)
        args = next((obj[k] for k in _ARG_KEYS if k in obj), None) if name else obj
        if name and args is None:
            # Any other key, by shape: the one object beside the tool's name.
            # GPT-OSS wrote {"tool": "…save_issue", "value": {…}}, and no list
            # of key names would have held "value".
            others = [v for k, v in obj.items() if k not in _NAME_KEYS and isinstance(v, dict)]
            args = others[0] if len(others) == 1 else None
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                continue
        if not isinstance(args, dict):
            continue
        if name is None:
            fits = [n for n, f in forms.items() if set(f.get("required") or ()) <= set(args)
                    and set(args) <= set(f.get("properties") or {})]
            name = fits[0] if len(fits) == 1 else None
        if name:
            found.append(SimpleNamespace(id=None, name=name, arguments=json.dumps(args)))
    return found


def _one_call(call, by_name: dict, question: str, *, org_id: str, user_id: str, email: str,
              found: list[str], looked: set[str], ran: dict, settings: ActionsSettings,
              out: _Drafting, drafted: dict, refused: dict, already: frozenset = frozenset(),
              read=None, call_id: str = "", check=None) -> tuple[str | None, bool]:
    """One tool call: what to tell the model (``None``: a drafted write, told
    once it is decided), and whether it was progress."""
    if call.name == SEARCH["function"]["name"] and read is not None:
        return _read_content(call, question, read=read, ran=ran, settings=settings, out=out)
    action = by_name.get(call.name)
    if action is None:
        return "There is no tool by that name. Use only the tools you were given.", False
    try:
        args = _plain(json.loads(call.arguments or "{}"))
    except json.JSONDecodeError:
        return "The arguments were not valid JSON. Call the tool again with a JSON object.", False
    if not isinstance(args, dict):
        return "The arguments must be a JSON object.", False

    if action.lookup:
        if getattr(action, "mcp_tool", None):
            from .mcp import asked_filters

            args = asked_filters(action.mcp_tool, args, question)
        key = action.name + json.dumps(args, sort_keys=True)
        if key in ran:
            return "You already ran this lookup; its answer is above. Use it.", False
        if len(ran) >= settings.mcp_max_lookups:
            return ("No more lookups are possible for this request. Draft from what you "
                    "already read, or reply saying what is missing."), False
        progress.report(f"Looking up in {action.label}")
        looked.add(action.server.name)
        try:
            token = action.token_for(org_id, user_id)
        except (ConfigurationError, OAuthReauthRequiredError):
            ran[key] = f"[{action.label}: the user must sign in to {action.server.name} again]"
            return ran[key], True
        ran[key] = action.run(args, token, timeout=settings.mcp_timeout_seconds)
        found.append(ran[key])
        unseen = []
        if _found_nothing(ran[key]) and getattr(action, "mcp_tool", None):
            from .mcp import unseen_filters

            unseen = unseen_filters(action.mcp_tool, args, found[:-1])
        loose = {k: v for k, v in args.items() if k not in unseen}
        wide = action.name + json.dumps(loose, sort_keys=True)
        if unseen and wide not in ran and len(ran) < settings.mcp_max_lookups:
            # A guessed filter value is dropped once, by the loop: told to drop
            # it, GPT-OSS kept ``state: "open"`` until the lookups ran out. A
            # lookup only reads, so asking again without it costs one call.
            ran[wide] = action.run(loose, token, timeout=settings.mcp_timeout_seconds)
            found.append(ran[wide])
            if not _found_nothing(ran[wide]):
                return (_fenced(ran[key]) + "\nNothing matched " + ", ".join(f"{k}={args[k]}" for k in unseen)
                        + ": a value no tool answer has shown. The same lookup without it:\n"
                        + _fenced(ran[wide]) + "\nTake from these only the items the QUESTION asks for, "
                        "by each item's own fields."), True
        if _found_nothing(ran[key]):
            # A title written without its dash ("UI test from slack 4") found
            # nothing, and the model gave up after one search.
            given = [f"{k}={v}" for k, v in args.items() if v not in (None, "", [], {}, False)
                     and not isinstance(v, (list, dict))]
            return _fenced(ran[key]) + "\n" + NOTHING_FOUND + (
                f" You filtered by {', '.join(given)}: a filter value the tool does not use exactly (a "
                "state, status, label or team named differently) matches nothing. Look up its real values "
                "with the tool that lists them, or drop that filter and pick from what comes back, before "
                "telling the user it cannot be found." if given else ""), True
        return _fenced(ran[key]), True

    # A write: drafted only when the asker asked for it, in their own words.
    if not asked_for(args.get("request_words"), question):
        refused.setdefault("unasked", (action.name, out.turn,
                                       "I only act when you ask me to do something directly, "
                                       "for example “file an issue in Platform about …”."))
        return ("Refused: request_words is not in the QUESTION. Copy the QUESTION's own words that "
                "ask for this action, in its order, and call again; if the QUESTION does not ask "
                "for it, do not call it."), False
    key = _form_key(action, args)
    if key in already:
        return DECIDED, False
    if key in drafted:
        return "Already drafted in this turn. Do not draft it again.", False
    problem = getattr(action, "form_problem", lambda _: None)(args)
    prepared: dict = {}
    if problem is None:
        problem = _precheck(action, args, question, org_id=org_id, user_id=user_id, email=email,
                            found=found, sources=tuple(out.sources), prepared=prepared)
    if problem:
        # A different form than any refused before is the model fixing it:
        # progress, so the loop gives it the turns to (bounded by MAX_STEPS).
        fresh = key not in refused
        refused[key] = (action.name, out.turn, problem)
        guide = getattr(action, "guide", lambda: "")()
        return (f"Refused, not drafted: {problem}. Fix the form (look up what you need) and call "
                f"again." + (f"\n{_fenced(guide)}" if guide else "")), fresh
    # A channel the QUESTION names as content, drafted from without reading
    # it: GPT-OSS wrote "pointers from #hb-testing today" it made up, which
    # reads as complete to any check of the text. Only a channel inside a
    # content part: "…then post both links in #hb-testing" names where a card
    # goes, and reading it put a week of the channel into a Linear issue.
    content = getattr(check, "content", None)
    asked = [p.casefold() for p in content()] if content else None
    unread = [c for c in dict.fromkeys(_CHANNEL.findall(question))
              if read is not None and c.casefold() not in _channels_read(out.messages or [])
              and (asked is None or any(f"#{c.casefold()}" in p for p in asked))
              and not any(isinstance(v, str) and v.lstrip("#").casefold() == c.casefold() for v in args.values())]
    # Fetched before drafted: content the request says comes from a read or a
    # lookup, drafted before anything was fetched, is made up whatever the
    # model ("a Google Sheet … with the open issues in BEN" became an empty
    # sheet, no lookup). And a card's lists must come from what was fetched.
    fetchers = {n for n, a in by_name.items() if getattr(a, "lookup", False)} | {SEARCH["function"]["name"]}
    seen = _fetched(out.messages or [], fetchers)
    unfetched = [] if seen else list(getattr(check, "needs", lambda *_: [])(action.label, args))
    untraced = _untraced(args, [question, *found, *seen])
    missing = (check(action.label, args) if check is not None else []) + \
        [f"what was said in #{c}" for c in unread] + \
        unfetched + (["items taken from what was read or looked up"] if untraced else [])
    missing = list(dict.fromkeys(missing))
    first = action.name not in out.checked
    out.checked.add(action.name)
    if missing:
        out.gaps[action.name] = missing
        if first:
            # One chance to make the card whole; after it, what is still
            # missing is said to the person rather than the card shown as done.
            return ("Refused, not drafted: this card does not yet include what the QUESTION asks "
                    f"of it: {'; '.join(missing)}. A summary, list or pointers are written in your own "
                    "words from what you read, never the messages pasted as they were. Read or look up "
                    "what you need, then call the tool again with it included, keeping everything else "
                    "this call had (its links too)."
                    + "".join(f" Read #{c} first: {SEARCH['function']['name']} with slack_channel "
                              f'"{c}".' for c in unread)
                    + (" Nothing has been read or looked up yet: call the lookup or read tool that has "
                       "it, then draft from its answer." if unfetched else "")
                    + (" Most items in its lists are in nothing you read or looked up: take them from "
                       "those answers." if untraced else "")), True
    else:
        out.gaps.pop(action.name, None)
    # The same card in other words: GPT-OSS left GitHub's owner out, then after
    # approval wrote owner "bench"; Handbook filled both as sana18-syv.
    done = _form_key(action, prepared["args"]) if isinstance(prepared.get("args"), dict) else key
    if done in already:
        return DECIDED, False
    if done in drafted:
        return "Already drafted in this turn. Do not draft it again.", False
    did = _did(action, prepared.get("args") or args, args.get("request_words"))
    if did in already:
        return DONE_BEFORE, False
    needs = _refs(args) & {n for _a, other, _c in out.picked for n in _names(other)}
    if needs:
        # GPT-OSS drafted a Sheet "in the folder Final E2E" beside the card
        # making that folder; the Sheet ran first and found no folder.
        refused[key] = (action.name, out.turn, f"needs {next(iter(needs))} made first")
        return (f"Refused, not drafted: {next(iter(needs))} is made by another card of this request. "
                f"{_LATER}"), False
    if len(out.picked) >= MAX_ACTIONS:
        out.left_out.append(action.label)
        return f"Not drafted: at most {MAX_ACTIONS} cards at a time.", False
    out.picked.append((action, args, call_id))
    out.drafted_in[action.name] = out.turn
    drafted[key] = drafted[done] = len(out.picked)
    out.keys.append(done)
    out.did[call_id] = did
    out.forms[call_id] = sorted({key, done})
    progress.report(f"Preparing a card: {action.label}")
    return None, True


def _read_content(call, question: str, *, read, ran: dict, settings: ActionsSettings,
                  out: _Drafting) -> tuple[str, bool]:
    """The content read: only for words the asker wrote, each once, within
    the request's read budget. What it returns is a source of every draft."""
    try:
        given = json.loads(call.arguments or "{}")
        given = given if isinstance(given, dict) else {}
    except json.JSONDecodeError:
        given = {}
    words = str(given.get("content_words") or "").translate(TYPOGRAPHY).strip()
    # A "#channel" the content words name is Slack's own syntax for one: read
    # live even when the model left slack_channel empty (GPT-OSS did).
    # A channel the asker did not name is never read: it is searched as words.
    # A garbled one ("hb#hb-testing") falls back to the content words' own.
    named = [str(given.get("slack_channel") or "").translate(TYPOGRAPHY).strip(), *_CHANNEL.findall(words)]
    channel = next((c for c in named if c and asked_in(c.lstrip("#"), question)), "")
    try:
        # "days": 0 is today, as 1 is; GPT-OSS sent "0", it read a week, and
        # decided nothing was said today.
        days = None if given.get("days") in (None, "") else min(max(int(given["days"]), 1), MAX_READ_DAYS)
    except (TypeError, ValueError):
        days = None
    if not asked_in(words, question):
        return ("Refused: content_words must be the QUESTION's own words that name the content, "
                "copied exactly. Read only content the QUESTION asks you to draft from."), False
    key = f"read:{words.casefold()}:{channel.casefold()}:{days}"
    if key in ran:
        return "You already read this; its text is above. Use it.", False
    if len(ran) >= settings.mcp_max_lookups:
        return "No more reads are possible for this request. Draft from what you already read.", False
    progress.report("Reading company content")
    shown, passages = read(words, channel=channel or None, days=days)
    ran[key] = passages
    known = {s.document_id for s in out.sources}
    out.sources += [s for s in shown if s.document_id not in known]
    if not passages:
        return ("Nothing the user can see matches that. Draft nothing from it: say in one sentence "
                "that you couldn't find it."), True
    progress.report(f"Drafting from {len(shown)} {'source' if len(shown) == 1 else 'sources'}")
    if channel:
        # GPT-OSS read today's #hb-testing, then found "no pointers about the
        # checkout timeout" in it: a channel read is the whole channel, not matches.
        return (_fenced(passages) + f"\nThat is every message in #{channel.lstrip('#')} for that time, "
                "not search matches: all of it is what was discussed there. Draft from it."), True
    return _fenced(passages), True


def _plain(value):
    """The model's arguments with ``TYPOGRAPHY`` undone, nested values included."""
    if isinstance(value, str):
        return value.translate(TYPOGRAPHY)
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value
