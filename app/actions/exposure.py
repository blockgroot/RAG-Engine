"""Would this write show private content to people who cannot see it? (plan §4.3a)

Approval does not catch this: people approve what they asked for. So the
verdict is computed from facts -- the viewers of every private document the
draft drew on, against the destination's audience -- and the card states it.

Emails are compared lowercased. A person known to a document under an older
address (``user_email_aliases``) counts as an outsider here: that makes the
verdict stricter, never looser, which is the side to err on.
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
    """``asker`` is never an outsider: every source was filtered for them, even
    when they read it through a group or an old address their email is not
    literally in. So a destination only the asker can read is ``narrower``."""
    private = [s for s in sources if not s.public]
    if not private:
        return Exposure(NONE)
    titles = tuple(dict.fromkeys(s.title for s in private))
    if audience.public:
        return Exposure(PUBLIC, titles)
    if audience.members is None:
        return Exposure(UNKNOWN, titles)

    # Who may read ALL of it: one private source a member cannot open is
    # enough to make them an outsider.
    allowed = {v.lower() for v in private[0].viewers}
    for source in private[1:]:
        allowed &= {v.lower() for v in source.viewers}
    # A member whose email the tool would not give is someone we cannot vouch
    # for, so they count as an outsider rather than vanishing from the count.
    named = {m.lower() for m in audience.members if m} - {(asker or "").lower()}
    unnamed = sum(1 for m in audience.members if not m)
    outsiders = len(named - allowed) + unnamed
    verdict = WIDER if outsiders else NARROWER
    size = len({m.lower() for m in audience.members if m}) + unnamed
    return Exposure(verdict, titles, size, outsiders)
