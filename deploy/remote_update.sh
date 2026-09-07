#!/usr/bin/env bash
# Синхронизация кода на сервер и перезапуск сервиса.
# Использование: SSHPASS=... SERVER=root@144.31.75.106 ./deploy/remote_update.sh
set -euo pipefail

SERVER="${SERVER:-root@144.31.75.106}"
APP_DIR="/opt/yandex_afisha_tickets"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"
tar czf - \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='__pycache__' \
  --exclude='.env' \
  . | sshpass -e ssh -o StrictHostKeyChecking=no "$SERVER" "tar xzf - -C $APP_DIR"

sshpass -e ssh -o StrictHostKeyChecking=no "$SERVER" bash -s <<REMOTE
set -euo pipefail
cd $APP_DIR
. .venv/bin/pip install -r requirements.txt
chown -R afisha:afisha $APP_DIR
systemctl restart yandex-afisha-bot
systemctl --no-pager status yandex-afisha-bot
REMOTE
