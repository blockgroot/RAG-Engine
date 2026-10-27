#!/usr/bin/env bash
# Installs Handbook on this server: asks for what only you know, generates
# the secrets, writes .env, starts everything and waits until it is healthy.
#
#   ./install.sh
#
# Safe to run again: an existing .env is kept, and Handbook is (re)started
# with it.

source "$(dirname "$0")/lib.sh"

need_docker
command -v openssl > /dev/null || fail "openssl is needed to generate secrets. Install it and run again."

ask() {  # ask VAR "Question" [secret]
  local answer=""
  while [ -z "$answer" ]; do
    if [ "${3:-}" = secret ]; then
      read -r -s -p "$2: " answer; echo
    else
      read -r -p "$2: " answer
    fi
  done
  printf -v "$1" '%s' "$answer"
}

if [ -f .env ]; then
  say "Keeping the existing .env"
  domain="$(get_env HANDBOOK_DOMAIN)"
else
  say "A few questions. Everything else has a default."
  version="$(cat VERSION 2> /dev/null || true)"
  [ -n "$version" ] || ask version "Handbook version to install (e.g. 1.0.0)"
  ask domain "Domain for Handbook, pointing at this server (e.g. handbook.yourcompany.com)"
  domain="${domain#https://}"; domain="${domain#http://}"; domain="${domain%/}"
  ask admin_email "Your email (approves your company's sign-up)"
  ask llm_key "Claude API key" secret
  ask embedding_key "Jina API key (search)" secret
  echo "Email for sign-in links, through your SMTP server:"
  ask smtp_host "  SMTP host"
  ask smtp_user "  SMTP username"
  ask smtp_pass "  SMTP password" secret
  ask smtp_from "  Send from address"

  cp .env.example .env
  chmod 600 .env
  set_env HANDBOOK_VERSION "$version"
  set_env HANDBOOK_DOMAIN "$domain"
  set_env POSTGRES_PASSWORD "$(openssl rand -hex 32)"
  set_env AUTH_JWT_SECRET "$(openssl rand -hex 32)"
  # A Fernet key: 32 random bytes, URL-safe base64.
  set_env AUTH_ENCRYPTION_KEYS "$(openssl rand -base64 32 | tr '+/' '-_')"
  set_env LLM_API_KEY "$llm_key"
  set_env EMBEDDING_API_KEY "$embedding_key"
  set_env EMAIL_SMTP_HOST "$smtp_host"
  set_env EMAIL_SMTP_USERNAME "$smtp_user"
  set_env EMAIL_SMTP_PASSWORD "$smtp_pass"
  set_env EMAIL_SMTP_FROM "$smtp_from"
  set_env OWNER_NOTIFICATION_EMAIL "$admin_email"
  say "Settings written to .env. Keep a copy of it somewhere safe: it holds the key to every connected tool's sign-in."
fi

# Write the domain into the Slack and GitHub app templates.
sed -i.bak "s/HANDBOOK_DOMAIN/$domain/g" apps/slack-manifest.yml apps/github-app.html
rm -f apps/*.bak

say "Downloading Handbook"
pull_images
say "Starting Handbook (the first start takes a minute or two)"
docker compose up -d
wait_healthy

say "Handbook is running at https://$domain"
cat << EOF

Next:
  1. Open https://$domain/signup and sign up with your work email.
  2. Approve the request in the email sent to $(get_env OWNER_NOTIFICATION_EMAIL),
     then use the sign-in link that follows.
  3. Connect your tools: docs/CONNECTORS.md. The Slack and GitHub templates
     in apps/ already contain your domain.
EOF
