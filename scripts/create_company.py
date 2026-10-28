"""Create a self-hosted copy's one company and its first admin.

A self-hosted copy runs with SIGNUP_ENABLED=false, so the company cannot come
from the sign-up form and its approval email. install.sh runs this once the
API is healthy. Safe to run again: an email that already has an account is
left as it is.

Run (inside the api container):
    python scripts/create_company.py "Acme Inc" admin@acme.com
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.auth.users import create_admin, get_user_by_email
from app.db import close_pool
from app.vectorstore import build_vector_store


def create_company(name: str, email: str) -> bool:
    """True when created; False when ``email`` already has an account."""
    email = email.strip().lower()
    if get_user_by_email(email) is not None:
        return False
    org_id = build_vector_store().create_organization(name.strip())
    create_admin(email, org_id)
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 2 or "@" not in argv[1] or not argv[0].strip():
        print('usage: create_company.py "Company name" admin@company.com')
        return 2
    load_dotenv()
    try:
        created = create_company(argv[0], argv[1])
    finally:
        close_pool()
    if created:
        print(f"Created {argv[0]} with admin {argv[1]}.")
    else:
        print(f"{argv[1]} already has an account; nothing to create.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
