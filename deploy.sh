#!/bin/bash
# Deploys Shop Inventory on the VPS. Run from anywhere:  sudo bash deploy.sh
# Touches only this app (shop_inventory.service). Never restarts gunicorn.service
# (the school portal).
set -euo pipefail

APP_DIR=/var/www/shop_inventory
ENV_FILE=/etc/shop_inventory.env
BACKUP_DIR=/var/backups/shop_inventory
SERVICE=shop_inventory
SITE_URL="${SITE_URL:-https://lekepee.com.ng}"
KEEP_BACKUPS=20

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo: sudo bash deploy.sh" >&2
  exit 1
fi
[ -f "$ENV_FILE" ] || { echo "Missing $ENV_FILE - see DEPLOYMENT.md" >&2; exit 1; }

cd "$APP_DIR"
as_app() { sudo -u www-data "$@"; }
in_env() { as_app bash -c "set -a; . $ENV_FILE; set +a; cd $APP_DIR && $*"; }

echo "==> Backing up the database"
mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$BACKUP_DIR/pre-deploy-$STAMP.sqlite3"
# SQLite's own backup API gives a consistent copy even while the app is running.
python3 - "$APP_DIR/shop_inventory.sqlite3" "$BACKUP" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2])
src.backup(dst); dst.close(); src.close()
PY
echo "    saved $BACKUP"
# Keep only the newest backups.
ls -1t "$BACKUP_DIR"/pre-deploy-*.sqlite3 | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f

PREVIOUS=$(as_app git rev-parse HEAD)

echo "==> Pulling latest code (fast-forward only)"
as_app git pull --ff-only origin main
CURRENT=$(as_app git rev-parse HEAD)
if [ "$PREVIOUS" = "$CURRENT" ]; then
  echo "    already up to date ($CURRENT)"
else
  as_app git --no-pager log --oneline "$PREVIOUS..$CURRENT"
fi

echo "==> Installing dependencies"
as_app .venv/bin/pip install --quiet -r requirements.txt

echo "==> Checking configuration"
in_env ".venv/bin/python manage.py check"

echo "==> Applying migrations and collecting static files"
in_env ".venv/bin/python manage.py migrate --noinput"
in_env ".venv/bin/python manage.py collectstatic --noinput" | tail -1

echo "==> Restarting $SERVICE"
systemctl restart "$SERVICE"
sleep 2
if ! systemctl is-active --quiet "$SERVICE"; then
  echo "!! $SERVICE failed to start. Last log lines:" >&2
  journalctl -u "$SERVICE" -n 30 --no-pager >&2
  echo "!! Roll back code with: cd $APP_DIR && sudo -u www-data git reset --hard $PREVIOUS" >&2
  echo "!! Database backup: $BACKUP (restore only if a migration ran)" >&2
  exit 1
fi

echo "==> Post-deploy checks"
in_env ".venv/bin/python manage.py check --deploy" || echo "    (advisory warnings above are normal)"
in_env ".venv/bin/python manage.py audit_access" || true

CODE=$(curl -s -o /dev/null -w '%{http_code}' "$SITE_URL/accounts/login/" || echo 000)
echo "    login page: HTTP $CODE"
[ "$CODE" = "200" ] || { echo "!! Site did not answer 200 - check logs." >&2; exit 1; }
NF=$(curl -s "$SITE_URL/this-page-does-not-exist" | head -c 300)
if echo "$NF" | grep -qi "DEBUG\|You're seeing this error"; then
  echo "!! DEBUG mode appears to be ON. Check DEBUG in $ENV_FILE." >&2
  exit 1
fi

echo "Shop Inventory deployed ($CURRENT)."
