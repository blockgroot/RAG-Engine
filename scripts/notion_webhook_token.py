"""Show (or clear) the Notion webhook's verification token.

When a Notion webhook subscription is created, Notion POSTs a one-time token to
/webhooks/notion. Handbook stores it encrypted and never logs it. Run this on
the server to read it, then paste it into the subscription's Verify form in
Notion. Nothing else is needed: events are checked against the stored token
(NOTION_WEBHOOK_VERIFICATION_TOKEN, if set, wins over it).

The first token received is kept, so an unsigned request cannot replace it. To
register a new subscription, clear it first:

    python scripts/notion_webhook_token.py           # show it
    python scripts/notion_webhook_token.py --reset   # forget it, then re-register
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Show or clear the Notion webhook token.")
    parser.add_argument("--reset", action="store_true", help="Forget the stored token.")
    args = parser.parse_args(argv)
    load_dotenv()
    from app.api.webhooks import forget_notion_token, stored_notion_token
    from app.db import close_pool

    try:
        if args.reset:
            print("Stored token cleared." if forget_notion_token() else "No token was stored.")
            return 0
        token = stored_notion_token()
    finally:
        close_pool()
    if not token:
        print("No token stored yet. Create the subscription in Notion; it sends one here.")
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
