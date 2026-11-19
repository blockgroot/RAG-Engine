"""Delete one company and everything it holds, for good.

Every tenant table cascades from ``organizations`` (its ``chunks`` partition is
dropped by trigger), so one row delete removes the data. Not cascaded, and
removed here: the sign-up request that kept the admin's email, and uploaded
files in object storage, which no row points at once the delete commits.

Drive push channels are not stopped: they lapse within a week, and a
notification for a company that no longer exists is ignored.

Run (inside the api container), naming the company exactly as a second check:
    python scripts/delete_company.py <org-id> --confirm "Acme Inc"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv


def delete_company(org_id: str, confirm_name: str) -> tuple[bool, str]:
    """``(deleted, message)``. Refuses unless ``confirm_name`` is the company's name."""
    from app.attachments.store import drop_stored
    from app.db.connection import get_connection

    with get_connection(all_orgs=True) as conn:
        row = conn.execute(
            "SELECT name FROM organizations WHERE id = %s::uuid", (org_id,)
        ).fetchone()
        if row is None:
            return False, f"No company {org_id}."
        if row[0] != confirm_name:
            return False, f"--confirm must be the company's exact name ({row[0]!r})."
        keys = [k for (k,) in conn.execute(
            "SELECT storage_key FROM conversation_attachments "
            "WHERE org_id = %s::uuid AND storage_key IS NOT NULL", (org_id,),
        ).fetchall()]
        conn.execute("DELETE FROM org_signup_requests WHERE org_id = %s::uuid", (org_id,))
        conn.execute("DELETE FROM organizations WHERE id = %s::uuid", (org_id,))
    # After the commit: a storage failure must not undo the delete.
    for key in keys:
        drop_stored(key)
    return True, f"Deleted {row[0]} and {len(keys)} uploaded file(s)."


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Delete one company for good.")
    parser.add_argument("org_id")
    parser.add_argument("--confirm", required=True, help="The company's exact name.")
    args = parser.parse_args(argv)
    load_dotenv()
    from app.db import close_pool

    try:
        deleted, message = delete_company(args.org_id, args.confirm)
    finally:
        close_pool()
    print(message)
    return 0 if deleted else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
