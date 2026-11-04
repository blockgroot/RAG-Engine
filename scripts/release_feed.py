"""Add a release to the public list every Handbook install checks.

Run by .github/workflows/release.yml with the public repo
(blockgroot/handbook-releases) checked out:

    python scripts/release_feed.py 1.5.0 release-notes/1.5.0.md releases/   # write
    python scripts/release_feed.py 1.5.0 release-notes/1.5.0.md --check     # only parse

A release cannot be tagged without its notes file. The file starts with two
flags, then a blank line, then the notes; each "- " line is one point shown
in the app, and the whole file is published as the full notes:

    security: false
    manual_steps: false

    - Charts from uploaded spreadsheets
    - Faster Slack replies

manual_steps: true means the upgrade needs a person (new .env settings, new
app scopes, a changed install package), so no copy offers the Update button
across it. security: true marks the update as urgent in every admin's bell.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
NOTES_URL = "https://github.com/blockgroot/handbook-releases/blob/main/notes/{version}.md"
FLAGS = ("security", "manual_steps")


def parse_notes(text: str) -> tuple[dict, list[str], str]:
    """``(flags, points, body)``. Raises ValueError naming what is wrong."""
    head, _, body = text.partition("\n\n")
    flags: dict[str, bool] = {}
    for line in head.strip().splitlines():
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip().lower()
        if key not in FLAGS or value not in ("true", "false"):
            raise ValueError(f"unexpected header line: {line!r}")
        flags[key] = value == "true"
    missing = [f for f in FLAGS if f not in flags]
    if missing:
        raise ValueError(f"missing {', '.join(missing)} (true or false) at the top")
    points = [line[2:].strip() for line in body.splitlines() if line.startswith("- ")]
    if not points:
        raise ValueError("no notes: add at least one line starting with '- '")
    return flags, points, body.strip() + "\n"


def add_release(feed: dict, version: str, flags: dict, points: list[str], day: str) -> dict:
    """The feed with ``version`` added (or replaced), newest first."""
    entry = {
        "version": version,
        "date": day,
        "security": flags["security"],
        "manual_steps": flags["manual_steps"],
        "notes": points,
        "notes_url": NOTES_URL.format(version=version),
    }
    releases = [r for r in feed.get("releases", []) if r.get("version") != version]
    releases.append(entry)
    releases.sort(key=lambda r: tuple(int(p) for p in r["version"].split(".")), reverse=True)
    return {"releases": releases}


def main(argv: list[str]) -> int:
    if len(argv) != 3 or not VERSION_RE.match(argv[0]):
        print(__doc__)
        return 2
    version, notes_path, target = argv
    try:
        flags, points, body = parse_notes(Path(notes_path).read_text())
    except FileNotFoundError:
        print(f"No release notes: write {notes_path} before tagging v{version}.")
        return 1
    except ValueError as exc:
        print(f"{notes_path}: {exc}")
        return 1
    if target == "--check":
        print(f"{notes_path}: ok ({len(points)} notes)")
        return 0

    out = Path(target)
    feed_path = out / "releases.json"
    feed = json.loads(feed_path.read_text()) if feed_path.exists() else {}
    feed = add_release(feed, version, flags, points, date.today().isoformat())
    feed_path.write_text(json.dumps(feed, indent=2) + "\n")
    (out / "notes").mkdir(exist_ok=True)
    (out / "notes" / f"{version}.md").write_text(f"# Handbook {version}\n\n{body}")
    print(f"Added {version} to {feed_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
