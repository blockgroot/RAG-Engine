"""Manage who may self-serve POST /auth/signup (create a brand-new org).

The owner-email whitelist is DB-backed (owner_email_whitelist table), not an
env var — granting a new owner takes effect immediately, no redeploy. This
script is the only interface to it (deliberately no HTTP/session surface).

Run:
    python scripts/manage_owner_whitelist.py list
    python scripts/manage_owner_whitelist.py add <email>
    python scripts/manage_owner_whitelist.py remove <email>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python scripts/manage_owner_whitelist.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.auth import add_owner_email, list_owner_emails, remove_owner_email
from app.db import apply_schema, close_pool


def _cmd_list(args: argparse.Namespace) -> int:
    emails = list_owner_emails()
    if not emails:
        print("No whitelisted emails.")
        return 0
    for email in emails:
        print(email)
    return 0


def _cmd_add(args: argparse.Namespace) -> int:
    add_owner_email(args.email)
    print(f"Whitelisted: {args.email}")
    return 0


def _cmd_remove(args: argparse.Namespace) -> int:
    remove_owner_email(args.email)
    print(f"Removed: {args.email}")
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Manage the owner-email whitelist for POST /auth/signup."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List whitelisted emails.")
    list_parser.set_defaults(handler=_cmd_list)

    add_parser = subparsers.add_parser("add", help="Whitelist an email.")
    add_parser.add_argument("email")
    add_parser.set_defaults(handler=_cmd_add)

    remove_parser = subparsers.add_parser("remove", help="Remove an email from the whitelist.")
    remove_parser.add_argument("email")
    remove_parser.set_defaults(handler=_cmd_remove)

    args = parser.parse_args()

    apply_schema()
    try:
        return args.handler(args)
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
