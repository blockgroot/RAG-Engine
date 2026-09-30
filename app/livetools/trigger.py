"""WHEN a live read is worth its ~2 s (mode A only).

Live reads used to run whenever a refreshable item was among the top hits, so
"what's our leave policy?" paid for one as often as "is SYV-5 still blocked?".
Two cheap decisions now gate it, and neither adds a model call:

1. **Does the question ask about the CURRENT state of something?** The
   question classifier already runs for every chat question (beside the
   cosine probe), so it answers this in the same call (``AskIntent.needs_live``).
   No hardcoded word list: a phrase list cannot tell "has Rahul reviewed the
   PR?" from "how do reviews work here?", and the classifier already reads
   the question. With NO verdict (classifier down, or the field missing) the
   read goes ahead -- an outage may cost ~2 s, never a stale answer.
2. **Is the synced copy already fresh?** A tool whose last SUCCESSFUL sync is
   younger than ``FRESH_SECONDS`` is not read at all (``gateway``).

Mode B (the model picking an item on the refusal path) is not gated: the
synced copy already failed to answer there.
"""

from __future__ import annotations


def wants_live(needs_live: bool | None) -> bool:
    """Only an explicit "no" from the classifier skips the read."""
    return needs_live is not False
