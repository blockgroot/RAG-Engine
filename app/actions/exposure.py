"""Would this write show private content to people who cannot see it?

People approve what they asked for, so the verdict is computed: the viewers
of every private source against the destination's audience, stated on the
card. Emails compare lowercased; an old alias counts as an outsider (stricter).
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import Audience, Source

NONE = "none"          # no private source
NARROWER = "narrower"  # everyone in the audience can already see every private source
WIDER = "wider"        # someone in the audience cannot
UNKNOWN = "unknown"    # the audience could not be read
PUBLIC = "public"      # the destination is public: never allowed with a private source

#: Verdicts that need the person's ticked acknowledgement before approval.
NEEDS_ACK = frozenset({WIDER, UNKNOWN})


@dataclass(frozen=True)
class Exposure:
    """The verdict, plus the facts the card names."""

    verdict: str
    private_titles: tuple[str, ...] = ()
    audience_size: int | None = None
    outsiders: int | None = None


def assess(
    sources: tuple[Source, ...] | list[Source], audience: Audience, *, asker: str | None = None
) -> Exposure:
    """``asker`` is never an outsider (every source was filtered for them), so a
    destination only they can read is ``narrower``."""
    private = [s for s in sources if not s.public]
    if not private:
        return Exposure(NONE)
    titles = tuple(dict.fromkeys(s.title for s in private))
    if audience.public:
        return Exposure(PUBLIC, titles)
    if audience.members is None:
        return Exposure(UNKNOWN, titles)

    # One private source a member cannot open makes them an outsider.
    allowed = {v.lower() for v in private[0].viewers}
    for source in private[1:]:
        allowed &= {v.lower() for v in source.viewers}
    # A member with no email is unvouched: an outsider, not dropped.
    named = {m.lower() for m in audience.members if m} - {(asker or "").lower()}
    unnamed = sum(1 for m in audience.members if not m)
    outsiders = len(named - allowed) + unnamed
    verdict = WIDER if outsiders else NARROWER
    size = len({m.lower() for m in audience.members if m}) + unnamed
    return Exposure(verdict, titles, size, outsiders)
