import json
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .models import Customer, Product, Sale, SaleItem, StockMovement, StoreSettings, UserProfile


def cashier_api_required(view_func):
    """Restrict POS API endpoints to authenticated cashiers and administrators."""
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Authentication required."}, status=401)
        if request.user.role not in (UserProfile.ROLE_ADMIN, UserProfile.ROLE_CASHIER):
            return JsonResponse({"error": "You do not have permission to perform sales."}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapped


def _money(value, field_name):
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"Invalid {field_name}.")
    if not parsed.is_finite():
        raise ValueError(f"Invalid {field_name}.")
    return parsed.quantize(Decimal("0.01"))


def _existing_synced_sale(temp_receipt):
    if not temp_receipt:
        return None
    marker = f"temp: {temp_receipt}"
    movement = (
        StockMovement.objects.filter(movement_type=StockMovement.SALE, note__contains=marker)
        .order_by("id")
        .first()
    )
    if not movement:
        return None
    try:
        sale_id = int(movement.note.split("Offline Sale #", 1)[1].split(" ", 1)[0])
    except (IndexError, TypeError, ValueError):
        return None
    return Sale.objects.filter(pk=sale_id).first()


@cashier_api_required
@require_POST
def api_sync_offline(request):
    """Idempotently sync POS sales while treating server data as authoritative."""
    try:
        data = json.loads(request.body)
    except (TypeError, ValueError) as exc:
        return JsonResponse({"error": f"Invalid JSON payload: {exc}"}, status=400)

    sales_payload = data.get("sales")
    if not isinstance(sales_payload, list):
        return JsonResponse({"error": "'sales' must be a list."}, status=400)

    synced = []
    errors = []

    for sale_data in sales_payload:
        temp_receipt = str(sale_data.get("temp_receipt", "")).strip()
        if not temp_receipt or len(temp_receipt) > 120:
            errors.append({"temp_receipt": temp_receipt, "error": "A valid client transaction reference is required."})
            continue

        items_data = sale_data.get("items")
        if not isinstance(items_data, list) or not items_data:
            errors.append({"temp_receipt": temp_receipt, "error": "Sale contains no items."})
            continue

        try:
            with transaction.atomic():
                # Serialise sync processing on the singleton settings row. This makes
                # the legacy temp-reference lookup safe without altering existing Sale data.
                StoreSettings.objects.select_for_update().get_or_create(pk=1)

                existing_sale = _existing_synced_sale(temp_receipt)
                if existing_sale:
                    synced.append({
                        "temp_receipt": temp_receipt,
                        "server_receipt": existing_sale.receipt_number,
                        "sale_id": existing_sale.pk,
                        "duplicate": True,
                    })
                    continue

                requested = {}
                for item in items_data:
                    barcode = str(item.get("barcode", "")).strip()
                    try:
                        quantity = int(item.get("quantity", 0))
                    except (TypeError, ValueError):
                        raise ValueError(f"Invalid quantity for barcode '{barcode}'.")
                    if not barcode or quantity < 1:
                        raise ValueError("Every item requires a barcode and quantity of at least 1.")
                    requested[barcode] = requested.get(barcode, 0) + quantity

                products = {
                    product.barcode: product
                    for product in Product.objects.select_for_update().filter(
                        barcode__in=requested.keys(), is_active=True
                    )
                }
                missing = sorted(set(requested) - set(products))
                if missing:
                    raise ValueError(f"Product not found or inactive: {', '.join(missing)}")

                lines = []
                subtotal = Decimal("0.00")
                for barcode, quantity in requested.items():
                    product = products[barcode]
                    available = product.stock_on_hand
                    if quantity > available:
                        raise ValueError(
                            f"Insufficient stock for {product.name}. Requested {quantity}; available {available}."
                        )
                    unit_price = product.selling_price
                    line_total = unit_price * quantity
                    subtotal += line_total
                    lines.append((product, quantity, unit_price, line_total))

                discount_amount = _money(sale_data.get("discount_amount", 0), "discount amount")
                if discount_amount < 0 or discount_amount > subtotal:
                    raise ValueError("Discount must be between zero and the sale subtotal.")

                settings = StoreSettings.get_solo()
                tax_rate = settings.default_tax_rate if settings.enable_tax else Decimal("0.00")
                taxable_amount = subtotal - discount_amount
                tax_amount = (taxable_amount * tax_rate / Decimal("100.00")).quantize(Decimal("0.01"))
                final_total = taxable_amount + tax_amount
                amount_paid = _money(sale_data.get("amount_paid", final_total), "amount paid")
                if amount_paid < final_total:
                    raise ValueError(f"Amount paid cannot be less than {final_total}.")

                customer = None
                customer_id = sale_data.get("customer_id")
                if customer_id not in (None, ""):
                    customer = Customer.objects.filter(pk=customer_id).first()
                    if customer is None:
                        raise ValueError("Selected customer does not exist.")

                sale = Sale.objects.create(
                    cashier_name=request.user.get_full_name() or request.user.username,
                    customer=customer,
                    total=final_total,
                    amount_paid=amount_paid,
                    discount_amount=discount_amount,
                    tax_rate=tax_rate,
                    tax_amount=tax_amount,
                )

                for product, quantity, unit_price, line_total in lines:
                    SaleItem.objects.create(
                        sale=sale,
                        product=product,
                        quantity=quantity,
                        unit_price=unit_price,
                        line_total=line_total,
                    )
                    StockMovement.objects.create(
                        product=product,
                        movement_type=StockMovement.SALE,
                        quantity=-quantity,
                        note=f"Offline Sale #{sale.pk} (temp: {temp_receipt})",
                    )

                synced.append({
                    "temp_receipt": temp_receipt,
                    "server_receipt": sale.receipt_number,
                    "sale_id": sale.pk,
                    "duplicate": False,
                })
        except Exception as exc:
            errors.append({"temp_receipt": temp_receipt, "error": str(exc)})

    status = 200 if synced or not errors else 400
    return JsonResponse({"synced": synced, "errors": errors}, status=status)
