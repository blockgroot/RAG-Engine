"""What a person asked to be called (``users.display_name``).

It replaced a name guessed from the email, so it must round-trip exactly as
typed (spaces tidied), refuse nothing-at-all, and only ever touch the
caller's own row in their own organization.
"""

from __future__ import annotations

import pytest

from app.auth.users import create_admin, set_display_name
from app.db.connection import get_connection

from .conftest import requires_db


def test_an_empty_name_is_refused_before_the_database():
    with pytest.raises(ValueError, match="Enter a name"):
        set_display_name("u", "o", "   ")
    with pytest.raises(ValueError, match="under 60"):
        set_display_name("u", "o", "x" * 61)


@requires_db
def test_a_name_is_saved_tidied_and_only_in_its_own_org(store, org_cleanup):
    org = store.create_organization("Display Name Co")
    other = store.create_organization("Display Name Other")
    org_cleanup.extend([org, other])
    user = create_admin("display.name.test@example.com", org)

    assert set_display_name(user.id, org, "  Sana   Asiwal ") == "Sana Asiwal"
    with get_connection() as conn:
        assert conn.execute("SELECT display_name FROM users WHERE id = %s", (user.id,)).fetchone()[0] == "Sana Asiwal"
    with pytest.raises(ValueError, match="not found"):
        set_display_name(user.id, other, "Someone Else")
