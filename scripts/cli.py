"""Interactive policy-chat CLI — the single entry point for talking to the agent.

Consolidates the old ``ask.py`` (one-shot) and ``chat.py`` (multi-turn) scripts
into one clean, interactive session. It is a *thin shell* over the Phase 7
``PolicyAgent``: it does no retrieval/gate/generation itself — it drives the agent
and formats what comes back, surfacing the meaningful internals of each turn
(query rewriting, retrieval reuse, answer provenance, grounding citations) without
burying them in a wall of text.

Why ``rich``: a clean, glanceable terminal UI (panels, colour-coded provenance,
aligned citation list, terminal-width wrapping) would otherwise be a lot of
hand-rolled ANSI. ``rich`` is a small, pure-Python, presentation-only dependency
confined to this script — it is never imported by anything under ``app/`` (the
core runtime stays dependency-light, per CLAUDE.md §1), so it can't affect the
self-hosted image's runtime footprint.

Run:
    python scripts/cli.py                 # pick an organization interactively
    python scripts/cli.py <org_id>        # chat as a specific org straight away
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.agent import build_policy_agent
from app.agent.base import Agent, AgentResponse
from app.core.exceptions import ProviderError
from app.db import close_pool
from app.memory import build_conversation_store
from app.rag import shutdown_summary_folds
from app.vectorstore import build_vector_store
from app.vectorstore.base import VectorStore

_EXIT_WORDS = {"/exit", "/quit", "/q", "exit", "quit"}

# How each answer provenance is presented: (human label, colour).
_SOURCE_STYLE = {
    "policy": ("grounded in policy documents", "green"),
    "web": ("from a web search", "cyan"),
    "none": ("no grounded answer — internal fallback", "yellow"),
}


def _answer_panel(response: AgentResponse, text: str | None = None) -> Panel:
    """Build the colour-coded, Markdown-rendered answer panel.

    ``text`` overrides ``response.answer`` (used while streaming, when only a
    prefix of the final answer has arrived so far); omit it to render the full
    final answer. Markdown rendering means lists, **bold**, and headings the
    model emits (see prompts.py rule 6 — bullet lists for multi-fact answers)
    display formatted instead of showing raw "-"/"**" markup.
    """
    label, colour = _SOURCE_STYLE.get(response.source, (response.source, "white"))
    return Panel(
        Markdown((text if text is not None else response.answer).strip() or " "),
        title=f"[bold {colour}]{label}[/]",
        border_style=colour,
        padding=(1, 2),
    )


def render_turn(console: Console, question: str, response: AgentResponse) -> None:
    """Render one Q&A turn: the answer, its provenance, and the internals.

    Pure formatting over an ``AgentResponse`` — no agent/network here — so it can
    be exercised directly in tests.
    """
    console.print(_answer_panel(response))
    render_internals(console, question, response)


def render_internals(console: Console, question: str, response: AgentResponse) -> None:
    """Render the behind-the-scenes internals + citations for a turn (no answer)."""
    internals = Text(style="dim")
    if response.resolved_question and response.resolved_question != question:
        internals.append("↻ rewritten to: ", style="dim italic")
        internals.append(f"{response.resolved_question}\n", style="italic")
    if response.retrieval_reused:
        internals.append("◆ retrieval: reused the previous turn's chunks (no new search)")
    else:
        internals.append("◆ retrieval: fresh search")
    score = f"{response.top_score:.3f}" if response.top_score is not None else "n/a"
    internals.append(f"   ·   top score: {score}")
    if response.recovery_used:
        reason = response.recovery_reason or "unknown"
        improved = "improved" if response.retrieval_improved else "no score gain"
        internals.append(f"\n◆ recovery: {reason} ({improved})")
        if response.top_score_before is not None or response.top_score_after is not None:
            before = (
                f"{response.top_score_before:.3f}"
                if response.top_score_before is not None
                else "n/a"
            )
            after = (
                f"{response.top_score_after:.3f}"
                if response.top_score_after is not None
                else "n/a"
            )
            internals.append(f"   ·   score {before} → {after}")
        if response.recovery_queries:
            internals.append(f"\n◆ recovery queries: {'; '.join(response.recovery_queries)}")
    if response.latency_ms is not None:
        internals.append(f"\n◆ latency: {response.latency_ms:.0f} ms")
    console.print(internals)

    # The chunks that grounded the answer (none for web/fallback). One compact
    # line each — robust across terminal widths, unlike a multi-column table.
    if response.citations:
        console.print(Text(f"◆ grounded on {len(response.citations)} chunk(s):", style="dim"))
        for i, cit in enumerate(response.citations, 1):
            preview = " ".join(cit.content.split())[:72]
            score = f"{cit.score:.3f}" if cit.score is not None else "n/a"
            line = Text(style="dim")
            line.append(f"   [{i}] ")
            line.append(cit.reference, style="cyan")
            line.append(f"  ({score})  {preview}…")
            console.print(line, no_wrap=True, overflow="ellipsis")
    console.print()


def stream_turn(
    console: Console, question: str, agent: Agent, org_id: str, conversation_id: str
) -> AgentResponse:
    """Run one turn, showing the answer progressively if the agent supports it.

    ``PolicyAgent.answer_stream`` (not part of the abstract ``Agent`` contract —
    see its docstring) has already fully resolved the answer — gate, recovery,
    tone-compliance retry, everything — before this function ever runs; only
    the *reveal* to the terminal changes, from "the whole panel pops in at
    once" to "the panel grows chunk by chunk" via ``rich``'s ``Live`` display.
    Falls back to the plain blocking ``agent.answer`` for any ``Agent`` that
    doesn't implement streaming (e.g. a test fake, or a future non-policy agent
    that only implements the abstract contract).
    """
    answer_stream = getattr(agent, "answer_stream", None)
    if answer_stream is None:
        response = agent.answer(question, org_id, conversation_id=conversation_id)
        render_turn(console, question, response)
        return response

    chunks, response = answer_stream(question, org_id, conversation_id=conversation_id)
    text = ""
    with Live(_answer_panel(response, text), console=console, refresh_per_second=12) as live:
        for chunk in chunks:
            text += chunk
            live.update(_answer_panel(response, text))
    render_internals(console, question, response)
    return response


def converse(
    agent: Agent,
    org_id: str,
    conversation_id: str,
    prompt_fn,
    console: Console,
) -> int:
    """Run the interactive loop until the user exits. Returns the number of turns.

    ``prompt_fn()`` returns the next user line, or ``None`` (or raises
    ``EOFError`` / ``KeyboardInterrupt``) to end the session — injected so tests
    can drive the loop deterministically.
    """
    turns = 0
    while True:
        try:
            question = prompt_fn()
        except (EOFError, KeyboardInterrupt):
            question = None
        if question is None:
            break
        text = question.strip()
        if not text:
            continue
        if text.lower() in _EXIT_WORDS:
            break
        stream_turn(console, text, agent, org_id, conversation_id)
        turns += 1
    console.print("[dim]Session ended.[/]")
    return turns


def resolve_org(store: VectorStore, console: Console, arg_org: str | None) -> str | None:
    """Return the org to chat as: the CLI arg, else an interactive picker."""
    if arg_org:
        return arg_org
    try:
        orgs = store.list_organizations()
    except NotImplementedError:
        orgs = []
    if not orgs:
        return console.input("Enter org_id: ").strip() or None

    table = Table(title="Organizations", title_style="bold", header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("name")
    table.add_column("org_id", style="dim")
    table.add_column("docs", justify="right")
    for i, org in enumerate(orgs, 1):
        table.add_row(str(i), org.name, org.id, str(org.document_count))
    console.print(table)

    # Accept a row number or a full org_id from the table; re-prompt on anything
    # else (a name, a typo) instead of passing junk down to the DB as an org_id.
    # NB: parentheses, not [brackets] — rich would parse [...] as console markup.
    valid_ids = {org.id for org in orgs}
    while True:
        choice = console.input("Pick an org (row number, or paste an org_id; blank to cancel): ").strip()
        if not choice:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(orgs):
            return orgs[int(choice) - 1].id
        if choice in valid_ids:
            return choice
        console.print("[red]Not a valid choice — enter a row number or an org_id from the table above.[/]")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Interactive policy-chat CLI.")
    parser.add_argument("org_id", nargs="?", help="Organization to chat as (optional).")
    args = parser.parse_args()

    console = Console()
    try:
        store = build_vector_store()
        org_id = resolve_org(store, console, args.org_id)
        if not org_id:
            console.print("[red]No organization selected.[/]")
            return 2

        memory = build_conversation_store()
        agent = build_policy_agent(memory=memory)
        conversation_id = memory.create_conversation(org_id)

        console.print(
            Panel(
                Text.from_markup(
                    f"[bold]Policy chat[/]\n"
                    f"org: [cyan]{org_id}[/]\n"
                    f"conversation: [dim]{conversation_id}[/]\n\n"
                    "Ask a question and keep asking follow-ups in the same "
                    "conversation.\nType [bold]/exit[/] (or Ctrl-D) to quit."
                ),
                border_style="blue",
                padding=(1, 2),
            )
        )

        converse(agent, org_id, conversation_id, lambda: console.input("[bold cyan]you ›[/] "), console)
        return 0
    except ProviderError as exc:
        console.print(f"[red]Error:[/] {exc}")
        if exc.cause:
            console.print(f"[dim]cause: {exc.cause}[/]")
        return 1
    finally:
        shutdown_summary_folds(wait=True, timeout=30.0)
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
