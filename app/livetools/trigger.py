"""WHEN a live read is worth its ~2 s (mode A only).

Live reads used to run whenever a refreshable item was among the top hits, so
"what's our leave policy?" paid for one as often as "is SYV-5 still blocked?".
Two cheap decisions now gate it, and neither adds a model call:

1. **Does the question ask about the CURRENT state of something?** The
   question classifier already runs for every chat question (beside the
   cosine probe), so it answers this in the same call (``AskIntent.needs_live``).
   With no verdict -- classifier down, or skipped in a scope with nothing to
   chart -- the word rule below decides, so a dead classifier cannot switch
   live reads off entirely.
2. **Is the synced copy already fresh?** A tool whose last SUCCESSFUL sync is
   younger than ``FRESH_SECONDS`` is not read at all (``gateway``).

Mode B (the model picking an item on the refusal path) is not gated: the
synced copy already failed to answer there.
"""

from __future__ import annotations

import re

#: Words that ask about how things stand NOW. The fallback only -- the
#: classifier catches phrasings this cannot ("has Rahul reviewed the PR?").
_CURRENT_STATE = re.compile(
    r"\b(latest|status|state|progress|update[sd]?|now|currently|current|still|"
    r"yet|today|recent(?:ly)?|so far|done|finished|blocked|stuck|merged|"
    r"reviewed|resolved|closed|open|pending|in review|in progress|eta|"
    r"who(?:'s| is) (?:on|working on|handling))\b",
    re.IGNORECASE,
)


def wants_live(needs_live: bool | None, question: str | None) -> bool:
    """The classifier's verdict when it gave one, else the word rule."""
    if needs_live is not None:
        return needs_live
    return bool(_CURRENT_STATE.search(question or ""))
