#!/bin/bash
# Deploys Shop Inventory. Run:  sudo bash deploy.sh
# Quiet by design: one status line per step; full output only if a step fails,
# and the deploy stops at the first failure. Never touches gunicorn.service
# (the school portal).
#
# Everything lives inside main() so that the `git pull` replacing this file
# mid-run cannot confuse bash.
main() {
  set -uo pipefail
  APP_DIR=/var/www/shop_inventory
  ENV_FILE=/etc/shop_inventory.env
  BACKUP_DIR=/var/backups/shop_inventory
  SERVICE=shop_inventory
  SITE_URL="${SITE_URL:-https://lekepee.com.ng}"
  LOG=$(mktemp)
  trap 'rm -f "$LOG"' EXIT

  step() {  # step "label" command...
    local label=$1; shift
    if "$@" >"$LOG" 2>&1; then
      printf '  [ ok ]  %s\n' "$label"
    else
      printf '  [FAIL]  %s\n' "$label"
      sed 's/^/          /' "$LOG"
      echo
      echo "Deploy aborted at: $label"
      [ -n "${PREVIOUS:-}" ] && echo "Previous code version: $PREVIOUS (roll back: cd $APP_DIR && sudo -u www-data git reset --hard $PREVIOUS)"
      [ -n "${BACKUP:-}" ] && echo "Database backup: $BACKUP"
      exit 1
    fi
  }
  warn_step() {  # same, but a failure only warns
    local label=$1; shift
    if "$@" >"$LOG" 2>&1; then
      printf '  [ ok ]  %s\n' "$label"
    else
      printf '  [warn]  %s\n' "$label"; sed 's/^/          /' "$LOG"
    fi
  }
  as_app() { sudo -u www-data "$@"; }
  in_env() { as_app bash -c "set -a; . $ENV_FILE; set +a; cd $APP_DIR && $*"; }

  if [ "$(id -u)" -ne 0 ]; then echo "Run with: sudo bash deploy.sh" >&2; exit 1; fi
  step "Environment file present" test -f "$ENV_FILE"
  cd "$APP_DIR" || exit 1

  mkdir -p "$BACKUP_DIR"
  BACKUP="$BACKUP_DIR/pre-deploy-$(date +%Y%m%d-%H%M%S).sqlite3"
  backup_db() {
    python3 - "$APP_DIR/shop_inventory.sqlite3" "$BACKUP" <<'PY'
import sqlite3, sys
s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()
PY
    ls -1t "$BACKUP_DIR"/pre-deploy-*.sqlite3 | tail -n +21 | xargs -r rm -f
  }
  step "Database backed up" backup_db

  PREVIOUS=$(as_app git rev-parse HEAD)
  step "Code updated (git pull)" as_app git pull --ff-only origin main
  step "Dependencies installed" as_app .venv/bin/pip install --quiet -r requirements.txt
  step "Configuration check" in_env ".venv/bin/python manage.py check"
  step "Database migrated" in_env ".venv/bin/python manage.py migrate --noinput"
  step "Static files collected" in_env ".venv/bin/python manage.py collectstatic --noinput"

  step "Service restarted" systemctl restart "$SERVICE"
  sleep 2
  step "Service running" systemctl is-active --quiet "$SERVICE"

  login_ok() { [ "$(curl -s -o /dev/null -w '%{http_code}' "$SITE_URL/accounts/login/")" = "200" ]; }
  debug_off() { ! curl -s "$SITE_URL/this-page-does-not-exist" | head -c 400 | grep -qi "You're seeing this error"; }
  step "Site answers (login page 200)" login_ok
  step "Debug mode is off" debug_off
  warn_step "Deployment checks (check --deploy)" in_env ".venv/bin/python manage.py check --deploy"

  echo
  echo "Shop Inventory deployed ($(as_app git rev-parse --short HEAD))."
  exit 0
}
main "$@"
