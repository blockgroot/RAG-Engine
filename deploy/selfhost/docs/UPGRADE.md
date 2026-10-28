# Upgrading Handbook

New features and fixes arrive as new versions. Your data is never inside the images: documents, chats and settings live in the database and in `.env`, which an upgrade does not touch.

## Hearing about new versions

When a new version is out, Handbook tells your admins: an entry in the bell and on **Admin › Updates**, with what changed. A security fix is marked as urgent. To do this, Handbook reads our public list of releases a few times a day; nothing about your company is sent. To turn it off, set `UPDATE_CHECK_ENABLED=false` in `.env`.

We also email every release, with its notes, to the contact on your license. Nothing upgrades by itself: you choose when.

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

## Upgrading from Handbook (one click)

In **Admin › Updates**, read the notes and press **Update to <version>**. Handbook then:

1. backs up the database (into `backups/`, as `backup.sh` does);
2. downloads the new version and restarts Handbook, which is unavailable for a minute or two;
3. checks that it started, and **goes back to your previous version by itself** if it did not.

The page shows each step and reloads when it is done.

The button is not offered when a version needs steps on your server first (the notes say which). Then the page shows the command for the next section instead.

**How it works, and turning it off.** The button uses the `updater` service in `docker-compose.yml`. It is the only part of Handbook with access to Docker on this server, which it needs to restart Handbook. It accepts one request, protected by `UPDATER_TOKEN` in `.env`, to install a version on our public release list, and runs only Handbook's own images. If your security policy does not allow a container with Docker access, disable it in `docker-compose.override.yml`:

```yaml
services:
  updater:
    profiles: [off]
```

Admin › Updates then shows the command instead of the button. If our images are private to you, put the registry username and token we sent you in `.env` (`REGISTRY_USERNAME`, `REGISTRY_TOKEN`) so the updater can download them.

## Upgrading from the server

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
