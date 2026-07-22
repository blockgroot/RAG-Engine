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
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.agent import build_policy_agent
from app.agent.base import Agent, AgentResponse
from app.core.exceptions import ProviderError
from app.db import close_pool
from app.memory import build_conversation_store
from app.vectorstore import build_vector_store
from app.vectorstore.base import VectorStore

_EXIT_WORDS = {"/exit", "/quit", "/q", "exit", "quit"}

# How each answer provenance is presented: (human label, colour).
_SOURCE_STYLE = {
    "policy": ("grounded in policy documents", "green"),
    "web": ("from a web search", "cyan"),
    "none": ("no grounded answer — internal fallback", "yellow"),
}


def render_turn(console: Console, question: str, response: AgentResponse) -> None:
    """Render one Q&A turn: the answer, its provenance, and the internals.

    Pure formatting over an ``AgentResponse`` — no agent/network here — so it can
    be exercised directly in tests.
    """
    label, colour = _SOURCE_STYLE.get(response.source, (response.source, "white"))

    # The answer, in a colour-coded panel titled by its provenance.
    console.print(
        Panel(
            Text(response.answer.strip()),
            title=f"[bold {colour}]{label}[/]",
            border_style=colour,
            padding=(1, 2),
        )
    )

    # Behind-the-scenes internals for this turn, compact and dim.
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
        response = agent.answer(text, org_id, conversation_id=conversation_id)
        render_turn(console, text, response)
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

    choice = console.input("Pick an org [number or paste an org_id]: ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(orgs):
        return orgs[int(choice) - 1].id
    return choice or None


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
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
