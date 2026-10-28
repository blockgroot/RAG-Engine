"""install.sh creates a self-hosted copy's one company, and may run again.

Re-running the installer must not create a second company for the same admin.
"""

from __future__ import annotations

import uuid

from app.auth.users import get_user_by_email
from scripts.create_company import create_company
from .conftest import requires_db

pytestmark = requires_db


def test_creates_the_company_once(org_cleanup):
    email = f"admin-{uuid.uuid4().hex[:8]}@example.com"

    assert create_company("Selfhost Co", email.upper()) is True
    user = get_user_by_email(email)
    org_cleanup.append(user.org_id)
    assert user.role == "admin"

    assert create_company("Selfhost Co", email) is False
    assert get_user_by_email(email).org_id == user.org_id
