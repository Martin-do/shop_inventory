# Shop Inventory

Local-first inventory and checkout app for a small shop.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py createsuperuser
.\.venv\Scripts\python manage.py runserver 0.0.0.0:8000
```

Open `http://127.0.0.1:8000`.

Phones on the same Wi-Fi can open `http://LAPTOP-IP:8000`.

## Shop Use

Double-click `start_shop.bat` after setup. It starts the local server and opens the POS page.
