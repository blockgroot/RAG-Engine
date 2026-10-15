"""Answer feedback and the documentation gaps it reveals.

A refusal is logged without anybody asking; a thumb is logged when somebody
does. See the `feedback_and_gaps` block in `app/db/schema.sql` for why both
live in one table. ``topics`` groups them by meaning for the people who can
fix them.
"""

from .store import CHART_AGENT, RATING_REASONS, normalize_question, record_gap, record_rating
from .topics import Topic, assign_topics, assign_topics_soon, list_topics, mark_handled

__all__ = [
    "CHART_AGENT",
    "RATING_REASONS",
    "Topic",
    "assign_topics",
    "assign_topics_soon",
    "list_topics",
    "mark_handled",
    "normalize_question",
    "record_gap",
    "record_rating",
]
