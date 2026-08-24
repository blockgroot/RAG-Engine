"""Prompt-Driven Activity Scheduler: user-authored recurring reports.

An orchestrator over existing capabilities (oauth_connections, the source
readers, the LLM, email) rather than a new swappable capability — so, like
``app/rag/`` and ``app/jobs/``, it has no ``base.py``/factory: there is only
one way to run a scheduler, and nothing to abstract over.
"""

from __future__ import annotations

from .store import (
    Scheduler,
    SchedulerError,
    create_scheduler,
    delete_scheduler,
    list_schedulers,
    update_scheduler,
)

__all__ = [
    "Scheduler",
    "SchedulerError",
    "create_scheduler",
    "delete_scheduler",
    "list_schedulers",
    "update_scheduler",
]
