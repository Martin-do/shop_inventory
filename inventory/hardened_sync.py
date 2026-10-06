import json
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .access_control import has_access
from .audit_signals import log_event
from .models import AuditLog, Customer, Product, Sale, SaleItem, StockMovement, StoreSettings, UserProfile


def cashier_api_required(view_func):
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


@cashier_api_required
@require_POST
def api_sync_offline(request):
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
                existing_sale = Sale.objects.select_for_update().filter(client_reference=temp_receipt).first()
                if existing_sale:
                    synced.append({
                        "temp_receipt": temp_receipt,
                        "server_receipt": existing_sale.receipt_number,
                        "sale_id": existing_sale.pk,
                        "duplicate": True,
                    })
                    continue

                requested_items = []
                barcodes = set()
                for item in items_data:
                    barcode = str(item.get("barcode", "")).strip()
                    sale_unit = str(item.get("sale_unit", item.get("sale_mode", "unit")) or "unit").strip().lower()
                    if sale_unit not in {SaleItem.SALE_UNIT, SaleItem.SALE_PACK}:
                        raise ValueError(f"Invalid sale unit for barcode '{barcode}'.")
                    raw_sale_quantity = item.get("sale_quantity", item.get("quantity", 0))
                    try:
                        sale_quantity = int(raw_sale_quantity)
                    except (TypeError, ValueError):
                        raise ValueError(f"Invalid quantity for barcode '{barcode}'.")
                    if not barcode or sale_quantity < 1:
                        raise ValueError("Every item requires a barcode and quantity of at least 1.")
                    requested_items.append({
                        "barcode": barcode,
                        "sale_unit": sale_unit,
                        "sale_quantity": sale_quantity,
                    })
                    barcodes.add(barcode)

                products = {
                    product.barcode: product
                    for product in Product.objects.select_for_update().filter(barcode__in=barcodes, is_active=True)
                }
                missing = sorted(barcodes - set(products))
                if missing:
                    raise ValueError(f"Product not found or inactive: {', '.join(missing)}")

                lines = []
                subtotal = Decimal("0.00")
                requested_base_units = {}
                for requested in requested_items:
                    product = products[requested["barcode"]]
                    sale_unit = requested["sale_unit"]
                    sale_quantity = requested["sale_quantity"]

                    if sale_unit == SaleItem.SALE_PACK:
                        if not product.units_per_pack:
                            raise ValueError(f"{product.name} is not configured for pack sales.")
                        base_quantity = sale_quantity * product.units_per_pack
                        unit_price = product.effective_pack_selling_price
                        units_per_pack_snapshot = product.units_per_pack
                    else:
                        base_quantity = sale_quantity
                        unit_price = product.selling_price
                        units_per_pack_snapshot = None

                    line_total = unit_price * sale_quantity
                    subtotal += line_total
                    requested_base_units[product.barcode] = (
                        requested_base_units.get(product.barcode, 0) + base_quantity
                    )
                    lines.append({
                        "product": product,
                        "quantity": base_quantity,
                        "sale_unit": sale_unit,
                        "sale_quantity": sale_quantity,
                        "units_per_pack_snapshot": units_per_pack_snapshot,
                        "unit_price": unit_price,
                        "line_total": line_total,
                    })

                for barcode, base_quantity in requested_base_units.items():
                    product = products[barcode]
                    available = product.stock_on_hand
                    if base_quantity > available:
                        raise ValueError(
                            f"Insufficient stock for {product.name}. "
                            f"Requested {base_quantity} base units; available {available}."
                        )

                discount_amount = _money(sale_data.get("discount_amount", 0), "discount amount")
                if discount_amount < 0 or discount_amount > subtotal:
                    raise ValueError("Discount must be between zero and the sale subtotal.")
                if discount_amount and not has_access(request.user, "apply_discount"):
                    raise ValueError("This account is not allowed to apply discounts.")

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
                    cashier=request.user,
                    cashier_name=request.user.get_full_name() or request.user.username,
                    client_reference=temp_receipt,
                    customer=customer,
                    total=final_total,
                    amount_paid=amount_paid,
                    discount_amount=discount_amount,
                    tax_rate=tax_rate,
                    tax_amount=tax_amount,
                )

                for line in lines:
                    product = line["product"]
                    SaleItem.objects.create(
                        sale=sale,
                        product=product,
                        quantity=line["quantity"],
                        sale_unit=line["sale_unit"],
                        sale_quantity=line["sale_quantity"],
                        units_per_pack_snapshot=line["units_per_pack_snapshot"],
                        unit_price=line["unit_price"],
                        line_total=line["line_total"],
                    )
                    sale_description = (
                        f'{line["sale_quantity"]} pack(s)'
                        if line["sale_unit"] == SaleItem.SALE_PACK
                        else f'{line["sale_quantity"]} unit(s)'
                    )
                    StockMovement.objects.create(
                        product=product,
                        actor=request.user,
                        movement_type=StockMovement.SALE,
                        quantity=-line["quantity"],
                        note=f"Sale #{sale.pk} ({temp_receipt}) | {sale_description}"[:240],
                    )

                log_event(
                    AuditLog.ACTION_SYNC,
                    f"Offline sale {sale.receipt_number} synced",
                    sale,
                    metadata={"client_reference": temp_receipt, "item_count": len(lines)},
                    actor=request.user,
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
