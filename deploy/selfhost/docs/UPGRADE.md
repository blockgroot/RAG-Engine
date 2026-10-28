# Upgrading Handbook

New features and fixes arrive as new versions. Your data is never inside the images: documents, chats and settings live in the database and in `.env`, which an upgrade does not touch.

## Hearing about new versions

We email every release, with its notes, to the contact on your license. Each release is also listed on the release page we gave you, with its install package and notes. Nothing upgrades by itself: you choose when.

## Version numbers

| Change | Example | What it means for you |
| --- | --- | --- |
| Patch | 1.3.0 → 1.3.1 | Fixes only. Safe to take any time. |
| Minor | 1.3 → 1.4 | New features, still compatible. Nothing to change unless the release notes say so. |
| Major | 1.x → 2.0 | Needs an action from you. The release notes say exactly what. |

**Always read the release notes first.** Look for three things:
1. **New settings** to add to `.env`.
2. **Changes to your connector apps.** For example, a new feature may need a new Slack scope. Your apps are yours, so you add it in Slack's (or GitHub's, Google's...) app settings, then connect the tool again in Handbook once. Without this, the feature quietly does not work.
3. Anything else marked as required.

We support the current version and the one before it.

## How to upgrade

1. Make any `.env` or connector-app changes the release notes ask for.
2. Run:
   ```sh
   ./upgrade.sh 1.4.0
   ```
   It backs up the database, switches the version, downloads the new images, restarts Handbook and waits until it is healthy. It prints how to go back to your previous version if you need to.

When it starts, the new version updates the database on its own. There is no separate migration step.

<details>
<summary>Upgrading by hand instead</summary>

1. Back up: `./backup.sh`.
2. In `.env`, set `HANDBOOK_VERSION=1.4.0`.
3. `docker compose pull && docker compose up -d`
4. Check `docker compose ps` shows `api` as `healthy`.

</details>

**When the release notes say the install package changed** (for example a new service in `docker-compose.yml`), download the new `handbook-selfhost-<version>.zip` first. Copy its files over your Handbook folder, **keeping your `.env`, `backups/` and `apps/`**, then run `./upgrade.sh`. Most releases change only the images, and need just `./upgrade.sh`.

Keep your own changes to the Compose setup in a `docker-compose.override.yml` next to `docker-compose.yml`, so copying in a new package never overwrites them.

## If something goes wrong

Handbook does not undo database changes, so going back means restoring the backup from step 1:

1. Set `HANDBOOK_VERSION` back to the old version.
2. Restore the backup ([BACKUP.md](BACKUP.md), "Restoring").
3. Run `docker compose up -d`.

Then send us the error from `docker compose logs api worker`.
