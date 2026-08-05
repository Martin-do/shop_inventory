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

## Barcode Scanner Behaviour

Most USB barcode scanners act as keyboards and append Enter after each scan.

- On POS, scanner Enter adds the scanned item only. A short scan lock prevents accidental duplicate suffixes.
- Enter inside checkout/payment fields does not complete a sale. Use **Complete Sale** and then **Confirm & Submit**.
- On Receive Stock, scanner Enter performs product lookup only. Stock changes require an explicit **Confirm Stock Receipt** click.
- Sale reversals are admin-only and require a written reason.

## Validation

Pull requests run Django system checks, migration checks, and the complete test suite through GitHub Actions.
