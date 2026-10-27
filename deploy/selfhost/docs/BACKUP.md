# Backing up Handbook

## What to keep

| What | Where | Why |
| --- | --- | --- |
| **The database** | The `db` service | Everything Handbook knows: your company, users, synced documents, chats and settings |
| **The `.env` file** | Next to `docker-compose.yml` | Your settings, and the key that unlocks every connected tool's sign-in (`AUTH_ENCRYPTION_KEYS`). A database backup is of little use without it. |
| Chat file uploads | Cloudinary | Backed up by Cloudinary, under your account |

Keep `.env` apart from the database backups, somewhere access is limited: it holds every secret.

Synced content can always be fetched again from your tools. Chats, settings, and who connected what cannot.

## Taking a backup

From the folder with `docker-compose.yml`:

```sh
docker compose exec -T db pg_dump -U handbook -Fc handbook > handbook-$(date +%Y%m%d-%H%M).dump
```

This works while Handbook is running. Copy the file off the server.

**How often:** daily is a sensible default, and always right before an upgrade. A simple way to make it daily is a cron entry on the server (create the `backups` folder first):

```
0 2 * * * cd /path/to/handbook && docker compose exec -T db pg_dump -U handbook -Fc handbook > backups/handbook-$(date +\%Y\%m\%d).dump
```

Delete old files as you see fit.

## Restoring

This replaces everything in the database with the backup.

```sh
docker compose stop web api worker
docker compose exec -T db pg_restore -U handbook -d handbook --clean --if-exists < handbook-20261027-0200.dump
docker compose up -d
```

Use the `.env` from the same time as the backup, or at least the same `AUTH_ENCRYPTION_KEYS`.

## Using your own managed database

If you set `DATABASE_URL` to your cloud provider's Postgres, use that provider's automated backups instead. You still need to keep `.env` safe yourself.
