# Deploying Shop Inventory

The app shares a VPS with the school portal. Everything here is scoped to this
app; **never restart `gunicorn.service` or touch the portal's Nginx files when
deploying the inventory app** — `gunicorn.service` is the portal.

## How it is laid out on the server

| Thing | Where |
|---|---|
| Code (git checkout of `main`) | `/var/www/shop_inventory` |
| Python environment | `/var/www/shop_inventory/.venv` |
| App service | `shop_inventory.service` (gunicorn on `127.0.0.1:8015`) |
| Environment settings | `/etc/shop_inventory.env` |
| Database | `/var/www/shop_inventory/shop_inventory.sqlite3` |
| Nginx, public site | `/etc/nginx/sites-available/lekepee.com.ng` (HTTPS, proxies to 8015) |
| Nginx, old address | `/etc/nginx/sites-available/shop_inventory` (plain HTTP on port 8010) |
| Certificate | Let's Encrypt, renews automatically (`sudo certbot renew --dry-run` to test) |

The domain works like this: `lekepee.com.ng` (A record → the VPS IP) → Nginx on
443 → gunicorn on 8015 → Django. DNS only maps a name to a machine; Nginx is what
forwards to the port.

## One-time setup for the hardened release

The hardened settings refuse to start without a real `SECRET_KEY` and
`ALLOWED_HOSTS`, so create the environment file **before** the first deploy of
this release.

```bash
cd /var/www/shop_inventory && sudo -u www-data .venv/bin/python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Copy the printed key, then:

```bash
sudo cp /var/www/shop_inventory/.env.example /etc/shop_inventory.env
sudo nano /etc/shop_inventory.env
sudo chown root:www-data /etc/shop_inventory.env && sudo chmod 640 /etc/shop_inventory.env
```

Put the key in `SECRET_KEY`. While staff still use the old `:8010` address, also
enable the commented "transition" lines at the bottom of that file — without
them, logins over plain HTTP fail, because secure cookies are never sent over
HTTP.

The service unit must load that file and run as `www-data` (not root):

```bash
sudo systemctl cat shop_inventory.service
```

```ini
[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/shop_inventory
EnvironmentFile=/etc/shop_inventory.env
ExecStart=/var/www/shop_inventory/.venv/bin/gunicorn --workers 3 --bind 127.0.0.1:8015 shop_inventory.wsgi:application
```

```bash
sudo systemctl daemon-reload
```

## Deploying an update

Always back up the database first — the release includes a migration that
changes staff accounts.

```bash
sudo mkdir -p /var/backups/shop_inventory && sudo cp /var/www/shop_inventory/shop_inventory.sqlite3 /var/backups/shop_inventory/pre-deploy-$(date +%Y%m%d-%H%M%S).sqlite3
```

Then, in order:

```bash
cd /var/www/shop_inventory && sudo -u www-data git pull --ff-only origin main
```

```bash
sudo -u www-data .venv/bin/pip install -r requirements.txt
```

```bash
sudo -u www-data bash -c 'set -a; . /etc/shop_inventory.env; set +a; .venv/bin/python manage.py migrate --noinput && .venv/bin/python manage.py collectstatic --noinput'
```

```bash
sudo systemctl restart shop_inventory && sudo systemctl status shop_inventory --no-pager | head -8
```

If the service fails to start, the reason is almost always in:

```bash
sudo journalctl -u shop_inventory -n 40 --no-pager
```

A message about `SECRET_KEY` or `ALLOWED_HOSTS` means the environment file is
missing or incomplete.

### A safer replacement for `deploy.sh`

The existing `deploy.sh` restarts `gunicorn` (the **school portal**) instead of
`shop_inventory`, and its `chmod` line points into the portal's folder. Replace
its contents with:

```bash
#!/bin/bash
set -euo pipefail
cd /var/www/shop_inventory

mkdir -p /var/backups/shop_inventory
cp shop_inventory.sqlite3 "/var/backups/shop_inventory/pre-deploy-$(date +%Y%m%d-%H%M%S).sqlite3"

sudo -u www-data git pull --ff-only origin main
sudo -u www-data .venv/bin/pip install -r requirements.txt
sudo -u www-data bash -c 'set -a; . /etc/shop_inventory.env; set +a; \
  .venv/bin/python manage.py migrate --noinput && \
  .venv/bin/python manage.py collectstatic --noinput'

systemctl restart shop_inventory
systemctl is-active shop_inventory
echo "Shop Inventory deployed."
```

Run it as `sudo bash deploy.sh`. It leaves the portal alone.

## Checks after every deploy

```bash
sudo -u www-data bash -c 'set -a; . /etc/shop_inventory.env; set +a; cd /var/www/shop_inventory && .venv/bin/python manage.py check --deploy && .venv/bin/python manage.py audit_access'
```

- `check --deploy` should list no errors (a few advisory warnings are normal).
- `audit_access` lists every staff account, where its access comes from, and
  anything risky (for example accounts that can still reach the Django admin
  site, or that were never configured in the permission editor).

Confirm debug mode is off — a missing page must show a plain "Not Found", not a
styled debug page:

```bash
curl -s https://lekepee.com.ng/this-page-does-not-exist | head -5
```

## Closing the old port 8010

Only after staff have moved to `https://lekepee.com.ng`:

1. In `/etc/shop_inventory.env`, delete the transition lines (the `:8010` and
   old-host entries and the two `*_COOKIE_SECURE=0` lines).
2. Remove the old site and reload Nginx:
   ```bash
   sudo rm /etc/nginx/sites-enabled/shop_inventory && sudo nginx -t && sudo systemctl reload nginx
   ```
3. Close the firewall ports (8000 has nothing listening behind it):
   ```bash
   sudo ufw delete allow 8010 && sudo ufw delete allow 8010/tcp && sudo ufw delete allow 8000/tcp
   ```
   Check the result with `sudo ufw status numbered` — the IPv6 rules need
   deleting separately.
4. `sudo systemctl restart shop_inventory`

## Backups

The in-app Google Drive backup runs inside the web process, so each gunicorn
worker would start its own. Keep it off (`auto_backup_enabled` in Settings) and
use cron instead. Also copy the SQLite file and `media/` somewhere **off** this
server regularly; a backup on the same disk is not a backup.

```bash
sudo crontab -e
```

```cron
15 22 * * * cp /var/www/shop_inventory/shop_inventory.sqlite3 /var/backups/shop_inventory/nightly-$(date +\%a).sqlite3
```

That keeps the last seven nightly copies (Mon–Sun, overwritten weekly).

## When something breaks

```bash
sudo journalctl -u shop_inventory -n 50 --no-pager
```

```bash
sudo tail -50 /var/log/nginx/error.log
```
