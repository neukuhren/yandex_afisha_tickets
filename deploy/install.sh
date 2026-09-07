#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/yandex_afisha_tickets"
DB_NAME="afisha_tickets"
DB_USER="afisha"
DB_PASSWORD="${DB_PASSWORD:-afisha_secure_pass_change_me}"

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y python3 python3-venv python3-pip postgresql postgresql-contrib git

if ! id afisha >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin afisha
fi

mkdir -p "$APP_DIR"
chown -R afisha:afisha "$APP_DIR"

sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 || \
  sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"

cd "$APP_DIR"
python3 -m venv .venv
. .venv/bin/pip install --upgrade pip
. .venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
  cat > .env <<EOF
BOT_TOKEN=${BOT_TOKEN}
DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@localhost:5432/${DB_NAME}
SUPER_ADMIN_ID=5498976897
ADMIN_IDS=7846597750,1368175520
PARSE_INTERVAL_SECONDS=60
APPEARANCE_INTERVAL_SECONDS=30
APPEARANCE_DURATION_SECONDS=3600
EOF
  chown afisha:afisha .env
  chmod 600 .env
fi

PYTHONPATH=. . .venv/bin/python scripts/seed_initial_events.py || true

cp deploy/yandex-afisha-bot.service /etc/systemd/system/yandex-afisha-bot.service
chown -R afisha:afisha "$APP_DIR"
systemctl daemon-reload
systemctl enable yandex-afisha-bot
systemctl restart yandex-afisha-bot
systemctl --no-pager status yandex-afisha-bot
