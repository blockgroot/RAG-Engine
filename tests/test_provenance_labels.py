"""Every answer source the backend can emit must have a frontend label.

This has now shipped broken twice. CLAUDE.md §5 records the first:
``"insights"`` was missing from ``LABELS``, so a real Drive pie chart rendered
the pill as "No answer found". The second was ``"attachment"`` -- a correctly
summarised PDF, also labelled "No answer found".

Both are the same defect: ``ProvenanceStripe.tsx`` does
``LABELS[identity] || LABELS.none``, so an unknown identity does not fail
loudly, it silently claims the answer is a REFUSAL. The answer still renders,
so nothing looks broken except the one line that tells the reader whether to
trust it.

There is no frontend test infrastructure (CLAUDE.md §5), and adding a React
test stack to catch a dictionary key would be worse than the bug. This reads
the TSX as text from the Python suite that already runs in CI -- the two
files are the only place the backend and frontend vocabularies have to agree,
so one test at that seam is enough.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.core.answer_sources import ANSWER_SOURCES, SOURCE_NONE

_STRIPE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "frontend"
    / "components"
    / "ProvenanceStripe.tsx"
)


def _keys(record_name: str) -> set[str]:
    """The keys of a top-level `const <name>: Record<string, string> = {...}`."""
    source = _STRIPE.read_text()
    match = re.search(
        rf"const {record_name}: Record<string, string> = \{{(.*?)\n\}};",
        source,
        re.DOTALL,
    )
    assert match, f"{record_name} not found in {_STRIPE.name}"
    # Keys only: skip comment lines, which routinely contain colons.
    return {
        m.group(1)
        for line in match.group(1).splitlines()
        if not line.strip().startswith("//")
        for m in [re.match(r"\s*([A-Za-z_][\w]*)\s*:", line)]
        if m
    }


@pytest.mark.parametrize("record_name", ["LABELS", "COLORS"])
def test_every_answer_source_is_known_to_the_pill(record_name: str) -> None:
    missing = ANSWER_SOURCES - _keys(record_name)
    assert not missing, (
        f"{record_name} in ProvenanceStripe.tsx is missing {sorted(missing)}. "
        "An unknown source falls back to the 'none' entry, so a real grounded "
        "answer would render as 'No answer found'."
    )


def test_the_refusal_label_is_the_only_one_that_says_no_answer() -> None:
    """The fallback must stay distinguishable from a real source.

    If some other source were ever labelled "No answer found" too, the pill
    would stop being evidence of anything. Counted inside the LABELS block
    only -- the comments around it discuss the phrase.
    """
    source = _STRIPE.read_text()
    block = re.search(
        r"const LABELS: Record<string, string> = \{(.*?)\n\};", source, re.DOTALL
    ).group(1)
    assert block.count('"No answer found"') == 1
    assert f'{SOURCE_NONE}: "No answer found"' in block
