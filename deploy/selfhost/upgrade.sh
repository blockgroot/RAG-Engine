#!/usr/bin/env bash
# Moves Handbook to another version: backs up, switches, restarts, checks.
#
#   ./upgrade.sh 1.4.0
#
# Read the release notes first: they say if .env or your connector apps need
# a change.

source "$(dirname "$0")/lib.sh"

[ $# -eq 1 ] || fail "Usage: ./upgrade.sh <version>   (for example ./upgrade.sh 1.4.0)"
[ -f .env ] || fail "No .env here. Run ./install.sh first."
need_docker

new="${1#v}"
old="$(get_env HANDBOOK_VERSION)"
[ "$new" != "$old" ] || fail "Handbook is already on $new."

say "Backing up before the upgrade"
./backup.sh

rollback="To go back to $old: set HANDBOOK_VERSION=$old in .env, restore the
backup above (docs/BACKUP.md, \"Restoring\"), and run docker compose up -d."
# Shown whether the upgrade fails here or the problem is noticed later.
trap 'echo; echo "$rollback"' EXIT

say "Upgrading $old -> $new"
set_env HANDBOOK_VERSION "$new"
ensure_updater_token
pull_images
docker compose up -d
wait_healthy

say "Handbook is now on $new"
