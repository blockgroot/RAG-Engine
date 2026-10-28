#!/usr/bin/env bash
# Saves Handbook's database to backups/handbook-<date>.dump. Works while
# Handbook is running.
#
#   ./backup.sh
#
# Restoring: docs/BACKUP.md.

source "$(dirname "$0")/lib.sh"

[ -f .env ] || fail "No .env here. Run ./install.sh first."
need_docker

if [ -n "$(get_env DATABASE_URL)" ]; then
  say "This install uses your own database (DATABASE_URL), so its backups are your database provider's. Nothing to do here."
  exit 0
fi

# A dump holds everything Handbook stores: readable by you only.
umask 077
mkdir -p backups
file="backups/handbook-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db pg_dump -U handbook -Fc handbook > "$file" \
  || { rm -f "$file"; fail "The backup failed. Is Handbook running? (docker compose ps)"; }
[ -s "$file" ] || { rm -f "$file"; fail "The backup came out empty."; }

say "Saved $file ($(du -h "$file" | cut -f1))"
echo "Also keep a copy of .env somewhere safe, apart from these files."
