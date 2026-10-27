# Upgrading Handbook

New features and fixes arrive as new versions. Your data is never inside the images: documents, chats and settings live in the database and in `.env`, which an upgrade does not touch.

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

1. **Back up** (see [BACKUP.md](BACKUP.md)). This is your way back if anything goes wrong.
2. In `.env`, set the new version:
   ```
   HANDBOOK_VERSION=1.4.0
   ```
3. Make any `.env` or connector-app changes the release notes ask for.
4. Download and start the new version:
   ```sh
   docker compose pull
   docker compose up -d
   ```
5. Check it is running: `docker compose ps` shows `api` as `healthy`.

When it starts, the new version updates the database on its own. There is no separate migration step.

Upgrades also refresh `docker-compose.yml` from the new install package when it changes. Keep your own changes in a `docker-compose.override.yml` next to it, so they are never overwritten.

## If something goes wrong

Handbook does not undo database changes, so going back means restoring the backup from step 1:

1. Set `HANDBOOK_VERSION` back to the old version.
2. Restore the backup ([BACKUP.md](BACKUP.md), "Restoring").
3. Run `docker compose up -d`.

Then send us the error from `docker compose logs api worker`.
