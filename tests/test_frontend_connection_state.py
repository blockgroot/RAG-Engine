"""Pin that a source's connected state is never rendered before it is known.

`connections` starts as `[]` on both source-management pages, which is
indistinguishable from "this scope has connected nothing" — so an
already-connected provider rendered "Not linked yet" plus a Connect button until
the fetch landed. Not merely a cosmetic flash: clicking Connect on a connected
source starts a fresh OAuth grant, so the screen was inviting an action off state
it had not yet loaded. Same class as the workspace owner briefly seeing the
member-only view.

Source-level assertions in the style of test_frontend_api_proxy.py — there is no
React test harness here, and the property worth pinning (the loading gate exists
at all) is visible in the source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

_PAGES = [
    _REPO / "frontend" / "app" / "admin" / "connections" / "page.tsx",
    _REPO / "frontend" / "app" / "workspaces" / "[id]" / "page.tsx",
]


@pytest.mark.parametrize("page", _PAGES, ids=lambda p: p.parent.name)
def test_connection_cards_are_gated_on_the_list_having_loaded(page: Path):
    text = page.read_text()
    assert "loadingConnections" in text, (
        "no loading state for the connections list — an empty list would render "
        "as 'not connected' and offer Connect for a connected source"
    )
    # The gate must be resolved in a `finally`, so a failed fetch cannot leave
    # the cards stuck in the placeholder state forever.
    assert ".finally(() => setLoadingConnections(false))" in text
    # And it must actually guard the cards, not just exist.
    card_index = text.index("<ConnectionCard")
    assert "loadingConnections" in text[:card_index]


def test_the_sources_header_does_not_report_counts_before_they_are_known():
    """"0 linked" / "3 need attention" are derived from the unloaded list."""
    text = _PAGES[0].read_text()
    gate = text.index("loadingConnections ? (")
    linked = text.index("{linkedCount} linked")
    assert gate < linked, "the linked/attention chips must sit inside the loaded branch"
