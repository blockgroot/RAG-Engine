"""Detect robotic source-meta narration in grounded answers.

Mode A/B answers must state facts directly, not narrate retrieval
("the document says…", "according to the docs…"). Compliance uses one
structural regex over source nouns + meta verbs — not a hand-maintained
phrase laundry list that models route around with shorthand.
"""

from __future__ import annotations

import re

# Source nouns the model uses when talking *about* evidence instead of stating
# facts. Includes common shorthand (doc/docs) observed in live failures.
_SOURCE = r"(?:documents?|docs?|handbook|policy documents?|available documents?)"

_SOURCE_META_RE = re.compile(
    rf"(?:"
    rf"\baccording to the {_SOURCE}\b|"
    rf"\b(?:the )?provided handbook\b|"
    rf"\bnot defined in the {_SOURCE}\b|"
    rf"\b(?:i )?cannot give a definitive answer\b|"
    rf"\b(?:does(?:n't| not)|do not) explicitly (?:answer|state|mention)\b|"
    rf"\bnot explicitly (?:answer|state|mention)\b|"
    rf"\b(?:the )?{_SOURCE}\b.{{0,48}}?\b(?:"
    rf"says?|mentions?|state|states|"
    rf"do(?:es)? not|don't|doesn't|"
    rf"(?:do not|does not|don't|doesn't) contain|"
    rf"contain(?:s|ed)? any information"
    rf")\b|"
    rf"\b(?:do not|does not|don't|doesn't) contain any information about\b"
    rf")",
    re.IGNORECASE | re.DOTALL,
)


def uses_source_meta_language(text: str) -> bool:
    """True when ``text`` narrates sources instead of stating grounded facts."""
    if not text or not text.strip():
        return False
    return _SOURCE_META_RE.search(text) is not None
