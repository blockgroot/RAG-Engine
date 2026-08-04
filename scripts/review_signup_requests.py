"""Review org-creation signup requests: list / approve / reject.

The platform owner's only interface to the org_signup_requests queue —
deliberately a CLI, not a new HTTP/session surface (see CLAUDE.md §2/§4).
Approving creates the org + its admin user and emails a magic-link sign-in
link; rejecting records an optional reason and emails a decline notice.

Run:
    python scripts/review_signup_requests.py list [--all]
    python scripts/review_signup_requests.py approve <request_id>
    python scripts/review_signup_requests.py reject <request_id> [--reason TEXT]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python scripts/review_signup_requests.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from app.auth import (
    create_magic_link_token,
    list_signup_requests,
    approve_signup_request,
    reject_signup_request,
    send_signup_approved_email,
    send_signup_rejected_email,
)
from app.config.settings import ApiSettings
from app.core.exceptions import NotFoundError, ProviderError
from app.db import apply_schema, close_pool
from app.vectorstore import build_vector_store


def _cmd_list(args: argparse.Namespace) -> int:
    status = None if args.all else "pending"
    requests = list_signup_requests(status=status)
    if not requests:
        print("No requests." if args.all else "No pending requests.")
        return 0
    for r in requests:
        print(f"{r.id}  {r.status:9s}  {r.email:35s}  {r.company_name}  ({r.created_at})")
    return 0


def _build_magic_link(email: str) -> str:
    token = create_magic_link_token(email)
    base = (ApiSettings.from_env().frontend_url or "").rstrip("/")
    return f"{base}/verify?token={token}"


def _cmd_approve(args: argparse.Namespace) -> int:
    store = build_vector_store()
    try:
        request, org_id = approve_signup_request(args.request_id, store=store)
    except NotFoundError as exc:
        print(f"Approve failed: {exc}")
        return 1

    print(f"Approved. org_id={org_id}  email={request.email}")
    link = _build_magic_link(request.email)
    try:
        send_signup_approved_email(request.email, link)
    except ProviderError as exc:
        print(f"Warning: approval email failed to send: {exc}")
    return 0


def _cmd_reject(args: argparse.Namespace) -> int:
    try:
        request = reject_signup_request(args.request_id, reason=args.reason)
    except NotFoundError as exc:
        print(f"Reject failed: {exc}")
        return 1

    print(f"Rejected. email={request.email}")
    try:
        send_signup_rejected_email(request.email, args.reason)
    except ProviderError as exc:
        print(f"Warning: rejection email failed to send: {exc}")
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Review org-creation signup requests.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List signup requests.")
    list_parser.add_argument(
        "--all", action="store_true", help="Show every request, not just pending ones."
    )
    list_parser.set_defaults(handler=_cmd_list)

    approve_parser = subparsers.add_parser(
        "approve", help="Approve a pending request: create its org + admin."
    )
    approve_parser.add_argument("request_id")
    approve_parser.set_defaults(handler=_cmd_approve)

    reject_parser = subparsers.add_parser("reject", help="Reject a pending request.")
    reject_parser.add_argument("request_id")
    reject_parser.add_argument("--reason", default=None, help="Optional reason for the requester.")
    reject_parser.set_defaults(handler=_cmd_reject)

    args = parser.parse_args()

    apply_schema()
    try:
        return args.handler(args)
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main())
