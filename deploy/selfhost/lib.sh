# Shared by install.sh, upgrade.sh and backup.sh. Not run on its own.

set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nError: %s\n' "$*" >&2; exit 1; }

# set_env KEY VALUE: replace KEY's line in .env, or append it. The value is
# passed through the environment so no character in it (/, &, |) can break
# the edit. Written back into the same file, which keeps its owner-only
# permissions (a mv would replace it with a world-readable one).
set_env() {
  VAL="$2" awk -v key="$1" '
    BEGIN { val = ENVIRON["VAL"]; done = 0 }
    $0 ~ "^" key "=" { print key "=" val; done = 1; next }
    { print }
    END { if (!done) print key "=" val }
  ' .env > .env.tmp
  cat .env.tmp > .env
  rm -f .env.tmp
}

# The secret the API uses to ask the updater for an update. Added to an
# existing .env too, so installs made before the updater get one.
ensure_updater_token() {
  [ -n "$(get_env UPDATER_TOKEN)" ] || set_env UPDATER_TOKEN "$(openssl rand -hex 32)"
}

# get_env KEY: the value set in .env, or empty.
get_env() {
  awk -F= -v key="$1" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' .env
}

need_docker() {
  command -v docker > /dev/null || fail "Docker is not installed. See https://docs.docker.com/engine/install/"
  docker compose version > /dev/null 2>&1 \
    || fail "The Docker Compose plugin is missing. See https://docs.docker.com/compose/install/linux/"
}

# Pull the images, explaining the usual cause when the registry refuses.
# Log in to the image registry with the username and token in .env, when
# set. Private images need it; the updater uses the same two values.
registry_login() {
  local user token registry
  user="$(get_env REGISTRY_USERNAME)"; token="$(get_env REGISTRY_TOKEN)"
  [ -n "$user" ] && [ -n "$token" ] || return 0
  registry="$(get_env HANDBOOK_REGISTRY)"; registry="${registry:-ghcr.io/blockgroot}"
  printf '%s' "$token" | docker login "${registry%%/*}" -u "$user" --password-stdin > /dev/null \
    || fail "The registry refused REGISTRY_USERNAME / REGISTRY_TOKEN in .env. Check them with us."
}

pull_images() {
  docker compose pull || fail "Could not download Handbook's images. If the
error says 'unauthorized' or 'denied', check REGISTRY_USERNAME and
REGISTRY_TOKEN in .env (the username and token we sent you), then run this
again."
}

# Wait until the api service reports healthy (it applies database changes
# and checks its settings first), or show why it did not. An api that keeps
# restarting has refused a setting; that is said at once, not after 5 minutes.
wait_healthy() {
  local id status restarts
  for _ in $(seq 1 60); do
    id="$(docker compose ps -q api)"
    status="$(docker inspect --format '{{.State.Health.Status}}' "$id" 2> /dev/null || true)"
    [ "$status" = "healthy" ] && return 0
    restarts="$(docker inspect --format '{{.RestartCount}}' "$id" 2> /dev/null || echo 0)"
    [ "${restarts:-0}" -ge 2 ] && break
    sleep 5
  done
  local reason
  reason="$(docker compose logs --tail 60 api 2>&1 | grep -E "FAILED|Error|error" | tail -5 || true)"
  echo >&2
  if [ -n "$reason" ]; then echo "$reason" >&2; else docker compose logs --tail 20 api >&2; fi
  fail "Handbook did not start. The lines above name the problem (a 'FAILED'
line names the setting). Fix it in .env, then run this script again.
Full log: docker compose logs api"
}
