"""Walk the Google Forms path against a REAL form, without waiting on a sync.

The product classifies survey responses on the Google ingest job's success
hook, which means "does this work?" normally costs a folder change plus a full
re-ingest. This calls the same functions directly and prints what each step
saw, so a live walkthrough is one command:

    GOOGLE_FORMS_ENABLED=true python scripts/check_google_forms.py <org_id>

It is a diagnostic, not a test: it writes real `activity_facts` rows for the
org you name, because the thing being checked is whether real rows appear.
Pass --dry-run to stop after listing and reading.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import get_connection_config  # noqa: E402
from app.auth.credentials import get_live_connection_token  # noqa: E402
from app.config.settings import GoogleSettings  # noqa: E402
from app.db.connection import close_pool, get_pool  # noqa: E402
from app.insights.sentiment import KIND, PROVIDER, record_form_sentiment  # noqa: E402
from app.sources.google_forms import GoogleFormsReader  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("org_id")
    ap.add_argument("--workspace-id", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    settings = GoogleSettings.from_env()
    print(f"GOOGLE_FORMS_ENABLED = {settings.forms_enabled}")
    if not settings.forms_enabled:
        print(
            "  -> the product returns 0 here without reading anything. Set it "
            "and RECONNECT Google: the flag adds forms.responses.readonly to "
            "the OAuth scope, and an existing token does not have it."
        )
        return 1

    config = get_connection_config(
        args.org_id, "google", workspace_id=args.workspace_id
    ) or {}
    picked = list(config.get("form_ids") or [])
    print(f"allow-list (source_config.form_ids) = {picked or 'EMPTY'}")
    if not picked:
        print("  -> empty means read NOTHING. Pick a survey on the Sources page.")
        return 1

    token = get_live_connection_token(args.org_id, "google", args.workspace_id)
    reader = GoogleFormsReader(token)

    # Listing goes through DRIVE, so it works on the old scope too; reading
    # responses is what needs the new one. Separating them here is the point:
    # it tells "wrong account" apart from "did not reconnect".
    forms = reader.list_forms()
    print(f"\nvisible to this token: {len(forms)} form(s)")
    for ref in forms:
        mark = "*" if ref.form_id in picked else " "
        print(f" {mark} {ref.form_id}  {ref.title}")

    for ref in [f for f in forms if f.form_id in picked]:
        try:
            payload = reader.fetch_responses(ref)
        except Exception as exc:  # noqa: BLE001
            print(f"\n{ref.title}: could not read responses -- {exc}")
            print("  a 401/403 here means RECONNECT, not a permissions bug.")
            continue
        answers = list(payload.answers)
        print(f"\n{ref.title}: {len(answers)} free-text answer(s)")
        by_question: dict[str, int] = {}
        for a in answers:
            by_question[a.question_title] = by_question.get(a.question_title, 0) + 1
        for question, count in by_question.items():
            floor = "" if count >= 5 else "  <-- under the 5-response floor"
            print(f"  {count:>3}  {question[:60]}{floor}")

    if args.dry_run:
        print("\n--dry-run: stopping before classification.")
        return 0

    written = record_form_sentiment(
        args.org_id,
        workspace_id=args.workspace_id,
        reader=reader,
        form_ids=picked,
    )
    print(f"\nclassified and written: {written} activity_facts row(s)")

    with get_pool().connection() as conn:
        rows = conn.execute(
            """
            SELECT subject, actor, count(*)
            FROM activity_facts
            WHERE org_id = %s AND provider = %s AND kind = %s
            GROUP BY subject, actor ORDER BY 3 DESC
            """,
            (args.org_id, PROVIDER, KIND),
        ).fetchall()
    print(f"{PROVIDER}/{KIND} rows now in activity_facts: {len(rows)} group(s)")
    for subject, actor, count in rows:
        print(f"  {count:>3}  {subject} / {actor}")
    if rows and all(c < 5 for _, _, c in rows):
        print(
            "\nEvery group is under 5, so `min_group_count` suppresses them in "
            "SQL and the chart is correctly EMPTY. Add responses, do not lower "
            "the floor."
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        close_pool()
