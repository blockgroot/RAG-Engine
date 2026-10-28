"""New Handbook versions: telling admins, and updating a self-hosted copy."""

from .check import (
    Release,
    UpdateError,
    UpdateStatus,
    start_update,
    update_progress,
    update_status,
)

__all__ = [
    "Release",
    "UpdateError",
    "UpdateStatus",
    "start_update",
    "update_progress",
    "update_status",
]
