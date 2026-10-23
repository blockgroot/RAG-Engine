"""Act mode end to end: propose one write, then run it once it is approved.

``propose`` asks the model to fill in one action's form; code checks the
person asked for it (the quoted ``request_words`` are in their message),
resolves the target from their own words with their own token, works out who
would newly see private content, and stores the frozen proposal. Nothing is
written.

Company content is read only through the model's read tool
(``SEARCH``), for words the asker wrote, found the way Ask finds it. Every
passage it returns is a source; a request written entirely in the asker's own
words reads nothing, so it never warns about private threads it never used.

The loop is any agent's tool loop, paused at a write: the drafted call is
answered with what really happened once the person decides the card, and the
same conversation goes on (``continue_run``). The loop is ``loop.py``, its
checks on a draft ``checks.py``, and everything the model reads ``prompts.py``.

``confirm`` is the only path to a write. It re-checks everything that can
change between the card and the click (Act still on, the action still
enabled, the hourly cap, a working token), then claims the stored row -- the
single-execution guarantee -- and sends the STORED payload, never one from
the client. Its outcome is recorded whatever happens.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ..auth.user_connections import mark_user_needs_reauth
from ..config.settings import ActionsSettings, GuardSettings, RagSettings
from ..core.exceptions import (
    ActionConflict,
    ActionError,
    ActionOutcomeUnknown,
    ActionRejected,
    ConfigurationError,
    NotFoundError,
    OAuthReauthRequiredError,
)
from ..db.connection import DatabaseError, get_connection
from ..rag import progress
from ..security.untrusted import scrub_untrusted_text
from ..security.visibility import viewer_clause
from . import policy, store
from .base import Action, ActionContext, Source
from .draft import asked_in
from .exposure import NEEDS_ACK, PUBLIC, Exposure, assess
from .factory import get_action
from .store import ActionRow

from .checks import _completeness
from .loop import MAX_ACTIONS, _agent_loop, _Drafting, _form_key, _not_asked_yet
from .prompts import _decided, _fenced, build_revise_prompt

logger = logging.getLogger("actions")

#: Passages shown to the model for drafting. Few, because every one of them is
#: a source the exposure check must count.
CONTEXT_HITS = 6
#: The fields that carry drafted words. Every other field is a target, already
#: required to be in the asker's own words (``draft.require_named``).
DRAFT_FIELDS = ("title", "body", "text")
#: How a passage names the tool it came from (Ask's sources, not Act's tools).
SOURCE_NAMES = {"linear": "Linear", "github": "GitHub", "notion": "Notion",
                "google": "Google Drive", "slack": "Slack"}


#: A word of a request or a tool's text, for ranking when embeddings fail.
_TOOL_WORD = re.compile(r"[^\W_]{3,}")

#: Embeddings of tool texts, by text. A tool's text changes only when its
#: server does, so each is embedded once per process.
_TOOL_VECTORS: dict[str, list[float]] = {}


def _fit_tools(offered: list[Action], question: str, embedder, cap: int) -> list[Action]:
    """At most ``cap`` tools: each connector's ranked by how near its name and
    description are to the request in meaning, then taken in turns, so a
    request that spans connectors keeps each one's best. Every tool of every
    server costs tokens on each call, and OpenAI's models and Groq refuse more
    than 128 (with every tool of four servers on, every request failed).
    Narrowing is ranking only: what the model may do with a tool it is offered
    is checked as always."""
    if len(offered) <= cap:
        return offered
    from ..core.exceptions import EmbeddingProviderError

    texts = [f"{a.label}. {a.tool['function'].get('description', '')}"[:1000] for a in offered]
    try:
        missing = [t for t in dict.fromkeys(texts) if t not in _TOOL_VECTORS]
        if missing:
            _TOOL_VECTORS.update(zip(missing, embedder.embed(missing)))
        [q] = embedder.embed([question])

        def score(i: int) -> float:
            v = _TOOL_VECTORS[texts[i]]
            dot = sum(a * b for a, b in zip(q, v))
            norm = (sum(a * a for a in q) * sum(b * b for b in v)) ** 0.5
            return dot / norm if norm else 0.0
    except EmbeddingProviderError as exc:
        # Ranked by the words the request shares with each tool instead: "the
        # first 40" cut every later connector's last tools, and "…and share it
        # with Raj" was never offered Share a file.
        logger.warning("Act: ranking %d tools by meaning failed, ranking by words: %s", len(offered), exc)
        asked = set(_TOOL_WORD.findall(question.casefold()))

        def score(i: int) -> float:
            return float(len(asked & set(_TOOL_WORD.findall(texts[i].casefold()))))

    # A tool whose own name shares a word with the request comes first, then
    # by meaning: "…and share it with Raj as an editor" ranked Share a file
    # below eight other Google tools, and it was not offered at all.
    said = set(_TOOL_WORD.findall(question.casefold()))
    named = [len(said & set(_TOOL_WORD.findall(a.label.split(":", 1)[-1].casefold()))) for a in offered]
    by_server: dict[str, list[int]] = {}
    for i in sorted(range(len(offered)), key=lambda i: (named[i], score(i)), reverse=True):
        server = getattr(offered[i], "server", None)
        by_server.setdefault(server.id if server else offered[i].label, []).append(i)
    keep: list[int] = []
    queues = list(by_server.values())
    while len(keep) < cap:
        keep += [queue.pop(0) for queue in queues if queue][:cap - len(keep)]
    return [offered[i] for i in sorted(keep)]


@dataclass(frozen=True)
class ActOutcome:
    """What the Act page shows: a sentence, and a card per proposal.

    ``action``/``exposure`` are the first draft (one-card callers);
    ``drafts`` is every ``(row, exposure)`` the request made."""

    reply: str
    action: ActionRow | None = None
    exposure: Exposure | None = None
    drafts: tuple = ()


def indexed_context(
    hits: list, org_id: str, viewer, workspace_id: str | None = None, *, days: int | None = None,
) -> tuple[list[Source], str]:
    """Search hits from indexed documents as ``(sources, passages)``: unflagged,
    at most ``CONTEXT_HITS``, and only documents this viewer may read.

    A chunk the injection guard flagged is dropped whatever the guard's mode,
    because here text can turn into a write. Each document's viewers are read
    again with the visibility predicate, so a source is exactly as visible as
    Ask would make it. ``days``: only documents changed in that many days
    ("this week's Linear issues"); one with no known change date is left out,
    and the passages say the window was applied.
    """
    guard = GuardSettings.from_env()
    hits = [h for h in hits if h.injection_score is None or h.injection_score < guard.threshold]
    if not days:
        hits = hits[:CONTEXT_HITS]
    if not hits:
        return [], ""
    clause, params = viewer_clause(viewer, alias="d")
    recent = " AND d.source_last_modified >= now() - make_interval(days => %s)" if days else ""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT d.id::text, d.title, d.doc_is_public, d.doc_viewers, d.source_uri "
            "FROM documents d WHERE d.org_id = %s::uuid "
            "AND d.workspace_id IS NOT DISTINCT FROM %s::uuid "
            f"AND d.id = ANY(%s::uuid[]){recent}{clause}",
            (org_id, workspace_id, list({h.document_id for h in hits}), *([days] if days else []), *params),
        ).fetchall()
    docs = {
        r[0]: Source(r[0], r[1], bool(r[2]), tuple(v.lower() for v in (r[3] or ())),
                     r[4] if (r[4] or "").startswith(("https://", "http://")) else None)
        for r in rows
    }
    kept = [h for h in hits if h.document_id in docs][:CONTEXT_HITS]
    if not kept:
        return [], ""
    # Each passage names its tool, so a draft from several tools can say which
    # one a point came from instead of blending them.
    passages = (f"(Only content changed in the last {days} days.)\n\n" if days else "") + "\n\n".join(
        f"[{_tool_label(h)}{docs[h.document_id].title}]\n{scrub_untrusted_text(h.content)}"
        for h in kept
    )
    return list({h.document_id: docs[h.document_id] for h in kept}.values()), passages


def _tool_label(hit) -> str:
    name = SOURCE_NAMES.get(hit.source_provider or "")
    return f"{name} · " if name else ""


def store_search(embedder, vstore):
    """A search straight against the store: one meaning-only query for the
    words naming the content, each hit held to the 0.35 gate. Only for a
    caller with no Ask pipeline (tests); the app passes ``api.actions.ask_search``.

    Every search returns ``(sources, passages)``. The web and a Slack DM read
    company-wide as the asker; a Slack CHANNEL passes that channel's tag and
    the room's viewer, exactly like a channel reply.
    """

    def search(question, about, *, org_id, workspace_id=None, tags=None, viewer=None, days=None):
        vec = embedder.embed([about or question])[0]
        hits = vstore.query(org_id, vec, top_k=CONTEXT_HITS, workspace_id=workspace_id,
                            tags=tags, viewer=viewer)
        gate = RagSettings.from_env().similarity_threshold
        hits = [h for h in hits if h.score is not None and h.score >= gate]
        return indexed_context(hits, org_id, viewer, workspace_id, days=days)

    return search


def _only_askers_words(args: dict, question: str) -> bool:
    """True when every drafted field is text the asker typed themselves.

    Then no passage reached the draft, whatever the model was shown, so none
    is a source: "comment on X: shipped today" carries nothing private.
    Anything else keeps the rule that every passage shown counts.
    """
    drafted = [args.get(f) for f in DRAFT_FIELDS if args.get(f) not in (None, "")]
    return bool(drafted) and all(isinstance(v, str) and asked_in(v, question) for v in drafted)


def offered_actions(
    org_id: str, user_id: str, settings: ActionsSettings, slack_user: str | None = None,
    workspace_id: str | None = None,
) -> tuple[list[Action], str | None]:
    """The tools to offer, or why there are none, in the person's terms: the
    MCP tools an admin turned on, of servers this person signed in to."""
    if not policy.act_on(org_id):
        return [], "Your admin hasn't turned on Agent mode yet."
    if workspace_id is not None and not policy.act_on(org_id, workspace_id):
        return [], "Agent mode isn't turned on in this space. The space's owner can turn it on in the space's settings."
    from .mcp import mcp_actions
    from .mcp_store import list_servers

    offered = mcp_actions(org_id, user_id, workspace_id)
    if any(not a.lookup for a in offered):
        return offered, None
    off = policy.space_off(org_id, workspace_id)
    sources = policy.space_sources(org_id, workspace_id)
    # Why not: no tool switched on at all, none for this space's connectors or
    # switched off by its owner, or switched on where they have not signed in.
    on = [s for s in list_servers(org_id) if s.enabled
          and any(s.mode(t.get("name", "")) == "write" for t in s.tools)]
    waiting = [s.name for s in on if policy.server_in_space(s, sources)
               and any(s.mode(t.get("name", "")) == "write"
                       and not policy.off_in_space(off, s.id, t.get("name", "")) for t in s.tools)]
    if on and not waiting:
        return [], ("No tools are on for Agent mode in this space: its connectors have none switched on, "
                    "or its owner switched them off.")
    if not waiting:
        return [], "Your admin hasn't switched on any tools for Agent mode yet."
    return [], f"Sign in to {' or '.join(waiting)} in Handbook (Account → Agent) to let it act for you."


def propose(
    question: str,
    *,
    org_id: str,
    user_id: str,
    email: str,
    viewer,
    llm,
    embedder,
    vstore,
    settings: ActionsSettings | None = None,
    surface: dict | None = None,
    scope_workspace_id: str | None = None,
    tags: list[str] | None = None,
    model: str | None = None,
    read_context=None,
    search=None,
) -> ActOutcome:
    """``surface`` says where the request came from (web, a Slack DM, a Slack
    channel) and is built by the caller from the session or the Slack event.
    ``viewer``, ``scope_workspace_id`` and ``tags`` say what the draft may
    read there; the caller decides them per surface. ``read_context`` replaces
    the search when the caller knows exactly what the request is about (a
    Slack thread it was asked in); it returns ``(sources, passages)``.
    ``search`` finds the content as ``(sources, passages)`` (Ask's retrieval,
    ``api.actions.ask_search``; ``store_search`` over ``vstore`` when not
    given)."""
    settings = settings or ActionsSettings.from_env()
    surface = dict(surface or {"kind": "web"})
    kind = surface.get("kind", "web")
    if policy.act_on(org_id) and not policy.surface_enabled(org_id, kind):
        return ActOutcome("Your admin hasn't turned on Agent mode here. You can use it in Handbook's chat.")
    offered, why_not = offered_actions(org_id, user_id, settings, surface.get("slack_user"),
                                       scope_workspace_id)
    if why_not:
        return ActOutcome(why_not)

    from ..llm.routed import use_model

    # The person's pick from the chat's model picker, validated by the route;
    # None is the default model (Slack, or no pick). Set here, not by the
    # caller, so a request never inherits another request's choice.
    use_model(model, org_id=org_id)
    offered = _fit_tools(offered, question, embedder, settings.mcp_max_tools)
    tools = [a.tool for a in offered]
    progress.report("Reading your request")
    find = search or store_search(embedder, vstore)
    # The person's own surfaces only: a channel drafts as the room.
    personal = kind in ("web", "slack_dm")

    def read(words: str, channel: str | None = None, days: int | None = None) -> tuple[list[Source], str]:
        # Found the way Ask finds it, from the whole request, with the words
        # naming the content searched alongside and ordering the results: the
        # action words ("create an issue … titled … checklist") ranked an
        # unrelated Atlas page above the Slack message the request meant.
        if read_context is not None:
            return read_context()
        if channel and personal:
            live = _channel_read(org_id, scope_workspace_id, channel, days, email)
            if live is not None:
                return live
        return find(question, words, org_id=org_id, workspace_id=scope_workspace_id, tags=tags,
                    viewer=viewer, **({"days": days} if days else {}))

    found: list[str] = []
    looked: set[str] = set()
    earlier, made = _earlier(org_id, user_id, scope_workspace_id, email) if personal else ("", [])
    if earlier:
        found.append(earlier)
    run = _agent_loop(llm, question, tools, offered, org_id=org_id, user_id=user_id, email=email,
                      found=found, looked=looked, ran={}, settings=settings,
                      in_thread=read_context is not None, read=read, earlier=earlier, sources=tuple(made),
                      check=_completeness(llm, question))
    if not run.picked:
        return _nothing_drafted(run)
    # An earlier card's result is a source only of the draft that uses it.
    drafted = json.dumps([args for _, args, _ in run.picked], ensure_ascii=False)
    used = [s for s in run.sources if s not in made or (s.uri and s.uri in drafted)]
    return _cards(run, question=question, sources=[*used, *_lookup_sources(looked, email)],
                  found=found, org_id=org_id, user_id=user_id, email=email, viewer=viewer, surface=surface,
                  scope_workspace_id=scope_workspace_id, settings=settings, model=model)


#: How far back, and how many, of the person's own approved cards the next
#: request is told about ("post the link of the issue we created").
EARLIER_HOURS = 12
EARLIER_LIMIT = 8


def _earlier(org_id: str, user_id: str, workspace_id: str | None, email: str) -> tuple[str, list[Source]]:
    """What the person's own approved cards made recently, for the next
    request: each request started fresh, so "DM Raj the link of the Linear
    issue we created" could not find it (minutes old, not synced, and its
    title typed without the dash). ``(lines, sources)``: the lines ground its
    ids and links like a lookup's answer; each source is private to them."""
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id::text, action, payload, result FROM actions WHERE org_id = %s::uuid "
                "AND user_id = %s::uuid AND workspace_id IS NOT DISTINCT FROM %s::uuid "
                "AND status = 'succeeded' AND executed_at > now() - make_interval(hours => %s) "
                "ORDER BY executed_at DESC LIMIT %s",
                (org_id, user_id, workspace_id, EARLIER_HOURS, EARLIER_LIMIT)).fetchall()
    except DatabaseError:
        logger.warning("Act: could not read the person's earlier cards", exc_info=True)
        return "", []
    lines, sources = [], []
    for aid, name, payload, result in rows:
        result = result or {}
        action = get_action(name, org_id)
        label = action.label if action else name
        url = result.get("url") if str(result.get("url") or "").startswith("https://") else None
        title = str((payload or {}).get("title") or "")[:200]
        # Cards made before ids were the tool's own stored Handbook's card id.
        ext = result.get("external_id") if str(result.get("external_id") or "") != aid else None
        lines.append(f"- {label}" + (f": \"{title}\"" if title else "")
                     + (f", id {ext}" if ext else "")
                     + (f", link {url}" if url else ""))
        sources.append(Source(f"act-result:{aid}", f"what {label} made", False, (email.lower(),), url))
    return "\n".join(lines), sources


def _channel_read(org_id: str, workspace_id: str | None, channel: str, days: int | None,
                  email: str) -> tuple[list[Source], str] | None:
    """A Slack channel read live with the company's (or this space's) Slack
    connection; ``None`` when there is none or it cannot read the channel."""
    from ..auth.credentials import get_live_connection_token
    from .slack_thread import channel_context

    try:
        token = get_live_connection_token(org_id, "slack", workspace_id)
    except (ConfigurationError, OAuthReauthRequiredError):
        return None
    progress.report(f"Reading #{channel.lstrip('#')} in Slack")
    return channel_context(token, channel, days or 7, email)


def _lookup_sources(looked: set[str], email: str) -> list[Source]:
    # What a lookup returned is that tool's content, private to the person who
    # read it: a source like any private passage, so moving it somewhere
    # others can read needs the tick, and somewhere public is refused.
    return [Source(f"mcp-lookup:{name}", f"what {name} returned", public=False, viewers=(email.lower(),))
            for name in sorted(looked)]


def _cards(run: _Drafting, *, question, sources, found, org_id, user_id, email, viewer, surface,
           scope_workspace_id, settings, model, step: int = 1, already: frozenset = frozenset()) -> ActOutcome:
    """The loop's drafts as stored cards, and the reply. The run is kept with
    its conversation, paused on those calls, so deciding the cards answers
    them and the request goes on (``continue_run``)."""
    from . import runs

    # Over the limit: the first ones are drafted and the rest is SAID, never
    # dropped silently (a fourth action simply vanished).
    left_out = run.left_out
    drafts, refusals, calls, did, forms = [], list(run.refused), {}, {}, {}
    messages = list(run.messages or [])
    for action, args, call_id in run.picked:
        progress.report(f"Preparing a card: {action.label}")
        made = _draft(action, args, question=question, sources=sources, lookups="\n".join(found),
                      org_id=org_id, user_id=user_id, email=email, viewer=viewer, surface=surface,
                      kind=surface.get("kind", "web"), scope_workspace_id=scope_workspace_id,
                      settings=settings)
        if isinstance(made, str):
            refusals.append(made)
            # No card will ever answer this call, so it is answered now.
            messages.append({"role": "tool", "tool_call_id": call_id, "content": f"Refused, not drafted: {made}"})
        else:
            drafts.append(made)
            calls[made[0].id] = call_id
            did[made[0].id] = run.did.get(call_id)
            forms[made[0].id] = run.forms.get(call_id) or []
    if not drafts:
        return ActOutcome(" ".join(dict.fromkeys(refusals)) or run.reply or "Nothing more to do.")
    reply = ("Here's what I'll do. Check it, then approve." if len(drafts) == 1 else
             f"Here are {len(drafts)} drafts. Each is sent only when you approve it.")
    if refusals:
        reply += " " + " ".join(dict.fromkeys(refusals))
    gaps = list(dict.fromkeys(p for a, _, _ in run.picked for p in run.gaps.get(a.name, ())))
    if gaps:
        reply += f" I couldn't include: {'; '.join(gaps)}. Edit the card, or ask again saying where it is."
    if left_out:
        reply += (f" I draft at most {MAX_ACTIONS} at a time, so I left out: "
                  f"{'; '.join(left_out)}. Ask again for {'it' if len(left_out) == 1 else 'them'}.")
    if step < runs.MAX_STEPS:
        try:
            runs.create(org_id=org_id, workspace_id=scope_workspace_id, user_id=user_id, surface=surface,
                        question=question, model=model, messages=messages, found=found, sources=sources,
                        drafted={"keys": sorted({*already, *(_form_key(a, args) for a, args, _ in run.picked), *run.keys}),
                                 "calls": calls, "did": did, "forms": forms},
                        action_ids=list(calls), ttl_seconds=settings.ttl_seconds, step=step)
        except DatabaseError:
            # The cards stand on their own; only going on after them is lost
            # (e.g. the table not created yet on this database).
            logger.warning("Act: could not keep the run; it will not continue", exc_info=True)
    return ActOutcome(reply, drafts[0][0], drafts[0][1], tuple(drafts))


def continue_run(*, org_id: str, user_id: str, email: str, viewer_for, action_id: str, llm, embedder,
                 settings: ActionsSettings | None = None) -> tuple[ActOutcome, dict] | None:
    """After a card is decided: when it belongs to a paused run and no card of
    that run is still pending, answer each paused call with what happened to
    its card and let the same conversation go on, as ``(outcome, surface)``:
    where the request was asked, so the caller shows the new cards there.
    ``None`` when there is nothing to continue, or nothing came of it (every
    card went through and nothing remains). Every new card is approved on its
    own, like the first.

    ``viewer_for(surface)`` is who the drafts read as there, decided by the
    caller per surface exactly as for the first request."""
    from . import runs
    from ..llm.routed import use_model

    settings = settings or ActionsSettings.from_env()
    try:
        claimed = runs.resume(org_id, user_id, action_id)
    except DatabaseError:
        logger.warning("Act: could not read the run to continue", exc_info=True)
        return None
    if claimed is None:
        return None
    run, cards = claimed
    try:
        kept = run.drafted if isinstance(run.drafted, dict) else {}
        calls, keys = kept.get("calls") or {}, frozenset(kept.get("keys") or ())
        # An approved card's action is done: not drafted again in other words.
        did = kept.get("did") or {}
        keys |= {did[aid] for aid, _name, status, _r in cards if status == "succeeded" and did.get(aid)}
        # A failed card may be drafted again once what it lacked exists: the
        # Sheet refused for a missing folder, then blocked as "already decided".
        forms = kept.get("forms") or {}
        keys -= {k for aid, _name, status, _r in cards if status == "failed" for k in forms.get(aid, ())}
        if not calls or set(calls) - {aid for aid, *_ in cards}:
            return None                    # a run from before calls were kept, or a card gone
        offered, why_not = offered_actions(org_id, user_id, settings, run.surface.get("slack_user"),
                                           run.workspace_id)
        if why_not:
            return ActOutcome(why_not), run.surface
        use_model(run.model, org_id=org_id)
        offered = _fit_tools(offered, run.question, embedder, settings.mcp_max_tools)
        found, sources, messages, statuses = list(run.found), run.source_list(), list(run.messages), []
        for aid, name, status, result in cards:
            if aid not in calls:
                continue
            action = get_action(name, org_id)
            label = action.label if action else name
            url = result.get("url") if str(result.get("url") or "").startswith("https://") else None
            said = _decided(label, status, result, url)
            if status == "succeeded":
                # What an approved write made is the person's own: its link may
                # go into the next draft, and its id fills the next form.
                found.append(said)
                sources.append(Source(f"act-result:{aid}", f"what {label} made", public=False,
                                      viewers=(email.lower(),), uri=url))
            messages.append({"role": "tool", "tool_call_id": calls[aid], "content": _fenced(said)})
            statuses.append(status)
        progress.report("Continuing your request")
        # ponytail: no content read when going on (the reader needs the
        # request's own search scope); the first turn reads what it needs.
        looked: set[str] = set()
        check = _completeness(llm, run.question)
        loop = _agent_loop(llm, run.question, [a.tool for a in offered], offered, org_id=org_id,
                           user_id=user_id, email=email, found=found, looked=looked, ran={}, settings=settings,
                           messages=messages, already=keys, sources=tuple(sources), check=check)
        if not loop.picked:
            # Silent only when nothing the request asked for is left: "…and share
            # it with Raj" stopped after the text, and its reason went unsaid.
            left = _not_asked_yet(loop.messages or [], {a.tool["function"]["name"]: a for a in offered}, check)
            if not left and not loop.refused and all(s in ("succeeded", "rejected") for s in statuses):
                return None                # all went through and nothing remains: nothing to say
            return _nothing_drafted(loop), run.surface
        return _cards(loop, question=run.question, sources=[*loop.sources, *_lookup_sources(looked, email)],
                      found=found, org_id=org_id,
                      user_id=user_id, email=email, viewer=viewer_for(run.surface), surface=run.surface,
                      scope_workspace_id=run.workspace_id, settings=settings, model=run.model,
                      step=run.steps + 1, already=keys), run.surface
    finally:
        runs.finish(org_id, run.id)


def _nothing_drafted(run: _Drafting) -> ActOutcome:
    """No card: why each try was refused (code's words, so a closing "Done."
    never hides a refusal), else the model's own reply."""
    return ActOutcome(" ".join(dict.fromkeys(run.refused)) or run.reply
                      or "Tell me what to do, for example: "
                         "“File a Linear issue in Platform about the login timeout.”")


def _draft(action, args, *, question, sources, org_id, user_id, email, viewer, surface, kind,
           scope_workspace_id, settings, lookups=""):
    """One form into one stored proposal, ``(row, exposure)``; or why not, in
    the person's terms. Each draft is checked on its own: its target, the
    person's access there, and who would newly see private content."""
    if _only_askers_words(args, question):
        sources = []
    name = action.label.split(":")[0]  # the server's name: "Linear", "GitHub"…
    try:
        token = action.token_for(org_id, user_id, kind)
        ctx = ActionContext(org_id, scope_workspace_id, user_id, email, question, token,
                            tuple(sources), viewer, surface, lookups)
        proposal = action.prepare(args, ctx)
    except OAuthReauthRequiredError:
        return f"Reconnect your {name} account in Handbook (Account → Agent), then ask again."
    except ConfigurationError:
        return f"Connect your {name} account in Handbook (Account → Agent) to let it act for you."
    except ActionError as exc:
        return str(exc)

    exposure = assess(sources, proposal.audience, asker=email)
    titles = ", ".join(exposure.private_titles)
    if exposure.verdict == PUBLIC:
        return (f"I won't do that: it would put content from private documents ({titles}) "
                "somewhere public. Pick a private destination, or ask without them.")
    if settings.block_wider and exposure.verdict in NEEDS_ACK:
        return f"I won't do that: people there can't see the private documents it uses ({titles})."
    row = store.create(
        org_id=org_id, workspace_id=scope_workspace_id, user_id=user_id, conversation_id=None,
        surface=kind, proposal=proposal, sources=sources,
        exposure=exposure.verdict, ttl_seconds=settings.ttl_seconds,
    )
    return row, exposure


def edit(
    *,
    org_id: str,
    user_id: str,
    action_id: str,
    shown_hash: str,
    fields: dict,
    settings: ActionsSettings | None = None,
) -> ActionRow:
    """Replace a pending draft's words with the person's own.

    Only drafted text changes (``Action.edit``); where it goes, the sources
    and the exposure verdict stay, because code cannot tell whether an edit
    still carries private content. The acknowledgement is given with the
    Approve click, so it is asked for again on the edited text.
    """
    settings = settings or ActionsSettings.from_env()
    if not policy.act_on(org_id):
        raise ActionRejected("Your admin has turned off Agent mode.")
    row = store.get(org_id, user_id, action_id)
    if row is None:
        raise NotFoundError("No such action.")
    action = get_action(row.action, org_id)
    if action is None:
        raise ActionRejected("This action is no longer available.")
    return store.update_payload(org_id, user_id, action_id, shown_hash=shown_hash,
                                payload=action.edit(row.payload, fields))


#: Longest instruction for a rewrite: a sentence or two about what to change.
MAX_INSTRUCTION = 1000


def revise(
    *,
    org_id: str,
    user_id: str,
    action_id: str,
    shown_hash: str,
    instruction: str,
    llm,
    model: str | None = None,
) -> ActionRow:
    """Rewrite a pending draft's words as the person asks, then save it as an
    edit (``Action.edit`` under ``store.update_payload``'s guards). Only words
    change; the target, sources and exposure stay, and the tick is asked
    again. Links and @-mentions survive only if the draft they saw or their
    instruction already had them. Nothing is sent."""
    if not policy.act_on(org_id):
        raise ActionRejected("Your admin has turned off Agent mode.")
    instruction = instruction.strip()[:MAX_INSTRUCTION]
    if not instruction:
        raise ActionRejected("Say what to change.")
    row = store.get(org_id, user_id, action_id)
    if row is None:
        raise NotFoundError("No such action.")
    if row.status != "proposed":
        raise ActionConflict(f"This was already {row.status}.")
    action = get_action(row.action, org_id)
    if action is None:
        raise ActionRejected("This action is no longer available.")
    current = action.editable(row.payload)

    from ..llm.routed import use_model

    use_model(model, org_id=org_id)
    reply = llm.generate(build_revise_prompt(current, instruction), max_tokens=4000)
    try:
        data = json.loads(re.search(r"\{.*\}", reply or "", re.S).group(0))
    except Exception:  # noqa: BLE001 - unreadable is "couldn't", never a guess
        data = None
    fields = {k: v for k, v in (data or {}).items() if k in current and isinstance(v, str)}
    if not fields or all(fields[k] == current[k] for k in fields):
        # Only the words are rewritten: "add name, status in the rows as well"
        # asked for a sheet's rows, which are part of the form, not its text.
        raise ActionError("I couldn't rework that. I can change only the drafted text; to change rows, "
                          "a target or a setting, cancel and ask again with them.")
    asked = "\n".join([*current.values(), instruction])
    return store.update_payload(org_id, user_id, action_id, shown_hash=shown_hash,
                                payload=action.edit(row.payload, fields, asked=asked))


def confirm(
    *,
    org_id: str,
    user_id: str,
    action_id: str,
    shown_hash: str,
    acknowledged: bool,
    settings: ActionsSettings | None = None,
) -> ActionRow:
    """Run an approved proposal once and record how it ended.

    Raises before the claim for anything that should leave the proposal
    approvable later (Act off, cap reached, reconnect needed); after the
    claim, every outcome is recorded and returned, never raised.
    """
    settings = settings or ActionsSettings.from_env()
    if not policy.act_on(org_id):
        raise ActionRejected("Your admin has turned off Agent mode.")
    row = store.get(org_id, user_id, action_id)
    if row is None:
        raise NotFoundError("No such action.")
    action = get_action(row.action, org_id)
    if action is None or not action.enabled_for(org_id, row.workspace_id):
        raise ActionRejected("Your admin has switched this action off.")
    if not policy.surface_enabled(org_id, row.surface):
        raise ActionRejected("Your admin has turned off Agent mode here.")
    if not policy.within_rate(org_id, user_id, settings=settings):
        raise ActionRejected("You've reached the hourly limit for actions. Try again later.")
    token = action.token_for(org_id, user_id, row.surface, payload=row.payload)

    claimed = store.claim(org_id, user_id, action_id, shown_hash=shown_hash,
                          acknowledged=acknowledged)
    try:
        made = action.execute(claimed.id, claimed.target, claimed.payload, token)
    except ActionOutcomeUnknown as exc:
        store.finish(org_id, claimed.id, status="unknown", result={"error": str(exc)})
    except OAuthReauthRequiredError as exc:
        mark_user_needs_reauth(org_id, user_id, action.provider)
        store.finish(org_id, claimed.id, status="failed", result={"error": str(exc)})
    except ActionError as exc:
        store.finish(org_id, claimed.id, status="failed", result={"error": str(exc)})
    except Exception:  # noqa: BLE001 - the request may have gone out; say so
        logger.exception("actions.execute crashed action=%s", claimed.action)
        store.finish(org_id, claimed.id, status="unknown",
                     result={"error": "Something went wrong while sending it."})
    else:
        url = made.url if (made.url or "").startswith("https://") else None
        store.finish(org_id, claimed.id, status="succeeded",
                     result={"external_id": made.external_id, "url": url})
    logger.info("actions.executed action=%s org=%s", claimed.action, org_id)
    return store.get(org_id, user_id, action_id)
