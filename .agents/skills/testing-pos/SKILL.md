---
name: testing-pos
description: End-to-end test the shop_inventory Django POS + inventory app via the browser. Use when verifying auth, product CRUD, POS checkout, or stock-ledger changes.
---

# Testing shop_inventory (Django POS)

Local-first Django app (SQLite, server-rendered templates, session cart). Stock is a ledger: `Product.stock_on_hand = SUM(StockMovement.quantity)`.

## Setup (before recording)
- Create venv + install Django, run migrations, then create a superuser:
  `DJANGO_SUPERUSER_PASSWORD=<pw> manage.py createsuperuser --username admin --email a@b.c --noinput`
- Run on a non-default port to avoid clashes: `manage.py runserver 127.0.0.1:8010`.
- Login is required (all views are `@login_required`). Login at `/accounts/login/`; anonymous hits redirect to `/accounts/login/?next=<path>`.
- The on-disk `shop_inventory.sqlite3` may contain pre-existing seed rows (e.g. `Biscuit`, `Juice`) — call these out as pre-existing, and test with a freshly-created product so assertions are unambiguous.

## Golden-path flow to cover
1. Auth gate: anonymous `/pos/` → redirect to login (URL contains `?next=/pos/`).
2. Create product at `/products/new/` — create form shows `Opening stock`, hides `is_active`.
3. Edit at `/products/<pk>/edit/` — edit form shows `is_active`, hides opening stock; editing keeps the same barcode with NO duplicate-barcode error (ModelForm self-exclusion).
4. POS checkout: add item, `Complete Sale` → receipt at `/sales/<id>/`; verify stock decrements by qty in the product list.
5. Oversell guard: adding qty > stock shows exact error "Only N units of <name> are in stock." and does NOT add to cart.
6. Deactivate toggle in product list flips Status Active↔Inactive and button label Deactivate↔Activate.
7. Inline category creation on the product form: the Add/Edit form has a **Category** dropdown (empty label "— Select a category —") plus a **New category** text field. Typing a name there creates the category via `Category.objects.get_or_create` in `ProductForm.save()` and assigns it (takes precedence over the dropdown). Verify by reopening the product's edit page — the dropdown should show the new category selected — and confirm it appears in the dropdown for the next product exactly once (get_or_create means reusing the name creates no duplicate). Creation happens in `save()`, not `clean()`, so an invalid submit (e.g. duplicate barcode) must NOT leave an orphan category.

## Gotcha — POS barcode input steals focus
`templates/inventory/pos.html` has JS that refocuses `#barcode-input` on EVERY `window` click:
```js
window.addEventListener("click", () => input.focus());
```
So clicking the quantity field then typing sends the digits back into the barcode field (you'll get "No active product was found for this barcode."). Workaround: click the barcode input, type the barcode, then press `Tab` to reach quantity, `ctrl+a` to select, then type the qty — do NOT click the quantity field. This may change if that script is removed.

## Assertions must use concrete values
Use exact numbers/text (stock `5 → 3`, price `15.00`, the literal oversell string), not "a message appears" — a broken build could still render the page.

## Devin Secrets Needed
None (local SQLite, self-created superuser).
