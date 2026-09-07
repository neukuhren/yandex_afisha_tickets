#!/usr/bin/env bash
# Обновление кода на сервере из публичного GitHub-репозитория.
# Использование:
#   SSHPASS=... ./deploy/remote_update.sh
#   SSHPASS=... BRANCH=master ./deploy/remote_update.sh
set -euo pipefail

SERVER="${SERVER:-root@144.31.75.106}"
APP_DIR="${APP_DIR:-/opt/yandex_afisha_tickets}"
REPO_URL="${REPO_URL:-https://github.com/neukuhren/yandex_afisha_tickets.git}"
BRANCH="${BRANCH:-cursor/yandex-afisha-tickets-bot-5aac}"

sshpass -e ssh -o StrictHostKeyChecking=no "$SERVER" bash -s <<REMOTE
set -euo pipefail
APP_DIR="$APP_DIR"
REPO_URL="$REPO_URL"
BRANCH="$BRANCH"

git config --global --add safe.directory "\$APP_DIR" 2>/dev/null || true

if [ -d "\$APP_DIR/.git" ]; then
  cd "\$APP_DIR"
  git fetch origin "\$BRANCH"
  git checkout "\$BRANCH"
  git reset --hard "origin/\$BRANCH"
else
  mkdir -p "\$APP_DIR"
  if [ -f "\$APP_DIR/.env" ]; then
    cp "\$APP_DIR/.env" /tmp/yandex_afisha_tickets.env.bak
  fi
  rm -rf "\$APP_DIR"
  git clone --branch "\$BRANCH" --depth 1 "\$REPO_URL" "\$APP_DIR"
  if [ -f /tmp/yandex_afisha_tickets.env.bak ]; then
    mv /tmp/yandex_afisha_tickets.env.bak "\$APP_DIR/.env"
    chown afisha:afisha "\$APP_DIR/.env"
    chmod 600 "\$APP_DIR/.env"
  fi
  cd "\$APP_DIR"
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -r requirements.txt
chown -R afisha:afisha "\$APP_DIR"
systemctl restart yandex-afisha-bot
systemctl --no-pager status yandex-afisha-bot
REMOTE
