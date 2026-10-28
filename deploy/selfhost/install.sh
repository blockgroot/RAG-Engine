#!/usr/bin/env bash
# Installs Handbook on this server. Run it twice:
#
#   ./install.sh    # 1st time: creates .env with its secrets, then stops
#   (fill in .env)
#   ./install.sh    # 2nd time: checks .env, starts Handbook, creates your company
#
# Safe to run again at any time: it applies what is in .env and (re)starts
# Handbook. To start over, run "docker compose down -v" (this deletes
# Handbook's data) and remove .env.

source "$(dirname "$0")/lib.sh"

need_docker
command -v openssl > /dev/null || fail "openssl is needed to generate secrets. Install it and run again."

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  set_env HANDBOOK_VERSION "$(cat VERSION 2> /dev/null || true)"
  set_env POSTGRES_PASSWORD "$(openssl rand -hex 32)"
  set_env AUTH_JWT_SECRET "$(openssl rand -hex 32)"
  # A Fernet key: 32 random bytes, URL-safe base64.
  set_env AUTH_ENCRYPTION_KEYS "$(openssl rand -base64 32 | tr '+/' '-_')"
  ensure_updater_token
  say "Created .env, with its secrets already generated."
  cat << 'EOF'

Next, open .env in an editor and fill in every value under "Fill these in".
The comments there say what each one is and where to find it. Then run
./install.sh again to start Handbook.

Keep a copy of .env somewhere safe: it holds the key to every connected
tool's sign-in.
EOF
  exit 0
fi

# Everything below runs with the .env you filled in.
missing=()
for key in HANDBOOK_VERSION HANDBOOK_DOMAIN HANDBOOK_COMPANY HANDBOOK_ADMIN_EMAIL \
           LLM_API_KEY EMBEDDING_API_KEY EMAIL_SMTP_HOST EMAIL_SMTP_USERNAME \
           EMAIL_SMTP_PASSWORD EMAIL_SMTP_FROM POSTGRES_PASSWORD AUTH_JWT_SECRET \
           AUTH_ENCRYPTION_KEYS; do
  [ -n "$(get_env "$key")" ] || missing+=("$key")
done
[ ${#missing[@]} -eq 0 ] || fail "These are still empty in .env: ${missing[*]}
Fill them in, then run ./install.sh again."

domain="$(get_env HANDBOOK_DOMAIN)"
[[ "$domain" =~ ^[A-Za-z0-9.-]+$ ]] || fail "HANDBOOK_DOMAIN must be only the domain, without
https:// or a path (for example handbook.yourcompany.com, or localhost)."
[[ "$(get_env HANDBOOK_ADMIN_EMAIL)" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] \
  || fail "HANDBOOK_ADMIN_EMAIL does not look like an email address."

ensure_updater_token

# Fresh copies of the Slack and GitHub app templates with this domain, so a
# changed domain is picked up on the next run.
for f in slack-manifest.yml github-app.html; do
  sed "s/HANDBOOK_DOMAIN/$domain/g" "apps/templates/$f" > "apps/$f"
done

say "Downloading Handbook"
registry_login
pull_images
say "Starting Handbook (the first start takes a minute or two)"
# A failed start is explained by wait_healthy, from the api's own log.
docker compose up -d || true
wait_healthy

admin_email="$(get_env HANDBOOK_ADMIN_EMAIL)"
docker compose exec -T api python scripts/create_company.py \
  "$(get_env HANDBOOK_COMPANY)" "$admin_email"

say "Handbook is running at https://$domain"
cat << EOF

Next:
  1. Open https://$domain and sign in with $admin_email.
  2. Follow docs/CHECKLIST.md: connect your tools (docs/CONNECTORS.md; the
     Slack and GitHub templates in apps/ already contain your domain), check
     the answers, and invite your colleagues.
EOF
