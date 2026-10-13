"""Answer feedback and the documentation gaps it reveals.

A refusal is logged without anybody asking; a thumb is logged when somebody
does. See the `feedback_and_gaps` block in `app/db/schema.sql` for why both
live in one table.
"""

from .store import (
    MAX_ROWS,
    RATING_REASONS,
    ChartGapRow,
    GapRow,
    RatedRow,
    list_chart_gaps,
    list_downvoted,
    list_gaps,
    normalize_question,
    rating_counts,
    record_gap,
    record_rating,
)

__all__ = [
    "ChartGapRow",
    "GapRow",
    "MAX_ROWS",
    "RATING_REASONS",
    "RatedRow",
    "list_chart_gaps",
    "list_downvoted",
    "list_gaps",
    "normalize_question",
    "rating_counts",
    "record_gap",
    "record_rating",
]
