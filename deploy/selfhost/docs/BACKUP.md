# Backing up Handbook

## What to keep

| What | Where | Why |
| --- | --- | --- |
| **The database** | The `db` service | Everything Handbook knows: your company, users, synced documents, chats, chat file uploads and settings |
| **The `.env` file** | Next to `docker-compose.yml` | Your settings, and the key that unlocks every connected tool's sign-in (`AUTH_ENCRYPTION_KEYS`). A database backup is of little use without it. |
| Chat file uploads, only if you set up Cloudinary | Cloudinary | Backed up by Cloudinary, under your account |

Keep `.env` apart from the database backups, somewhere access is limited: it holds every secret.

Synced content can always be fetched again from your tools. Chats, settings, and who connected what cannot.

## Taking a backup

From the Handbook folder:

```sh
./backup.sh
```

It saves the database to `backups/handbook-<date and time>.dump`, in the Handbook folder. This works while Handbook is running. `upgrade.sh` runs it for you before every upgrade.

The file is a compressed Postgres backup, readable only by the user who ran the script. It is not plain text: only `pg_restore` (below) reads it.

**How often:** daily is a sensible default. A simple way to make it daily is a cron entry on the server:

```
0 2 * * * /path/to/handbook/backup.sh
```

Delete old files as you see fit.

## Keep a copy off the server

`backups/` is on the same disk as the database. If the server or its disk is lost, the backups go with it. Copy them somewhere else on a schedule, for example to your cloud's object storage:

```
30 2 * * * aws s3 sync /path/to/handbook/backups s3://your-bucket/handbook-backups
```

Any tool your company already uses for off-site copies (`rsync`, `rclone`, your backup software) works the same way. Keep `.env` in a separate, more restricted place.

## Restoring

This replaces everything in the database with the backup. Use the name of the file you want from `backups/`.

```sh
docker compose stop web api worker
docker compose exec -T db pg_restore -U handbook -d handbook --clean --if-exists < backups/handbook-20261027-020000.dump
docker compose up -d
```

Use the `.env` from the same time as the backup, or at least the same `AUTH_ENCRYPTION_KEYS`.

## Using your own managed database

If you set `DATABASE_URL` to your cloud provider's Postgres, use that provider's automated backups instead. You still need to keep `.env` safe yourself.
