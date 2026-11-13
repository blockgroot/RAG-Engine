"""Answer feedback and the documentation gaps it reveals.

Refusals are logged automatically, thumbs when given, in one table.
``topics`` groups them by meaning for the people who can fix them.
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
