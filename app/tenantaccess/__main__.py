"""Print the replica's pg_hba.conf lines for every tenant login (hba.py)."""

from __future__ import annotations

from ..config.settings import TenantAccessSettings
from ..db import close_pool
from .hba import hba_lines
from .store import list_logins

try:
    print(hba_lines(list_logins(), TenantAccessSettings.from_env().database), end="")
finally:
    close_pool()
