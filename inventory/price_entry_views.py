"""Dedicated price-entry flow: one product at a time, saved as each one is finished.

Products created during stocktake/receiving often have no selling price yet. This
page walks through them like a quiz: show the product, take a unit price (and an
optional pack price), save, move to the next. Every save goes through
``Product.save()`` so the audit trail records it as an edit by the signed-in user.
"""
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from functools import wraps

from .access_control import has_access, permission_required
from .models import AuditLog, Product

QUEUE_LIMIT = 2000
HISTORY_LIMIT = 15
PRICE_FIELDS = {"selling_price", "pack_selling_price", "units_per_pack"}
MAX_PRICE = Decimal("9999999999.99")


def api_permission_required(codename):
    """Like permission_required, but answers JSON errors instead of redirecting."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return JsonResponse({"error": "Please log in again."}, status=401)
            if not has_access(request.user, codename):
                return JsonResponse({"error": "Your account cannot change prices."}, status=403)
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def _serialize(product, user):
    data = {
        "id": product.pk,
        "name": product.name,
        "variant": product.variant,
        "barcode": "" if product.barcode.startswith("MANUAL-") else product.barcode,
        "category": product.category.name if product.category_id else "",
        "selling_price": str(product.selling_price) if product.selling_price else "",
        "units_per_pack": product.units_per_pack or "",
        "pack_selling_price": str(product.pack_selling_price) if product.pack_selling_price is not None else "",
        "priced": bool(product.selling_price),
    }
    if has_access(user, "view_stock"):
        data["stock"] = product.stock_on_hand
    if has_access(user, "view_cost_price") or has_access(user, "change_cost_price"):
        data["cost_price"] = str(product.cost_price)
    return data


def _history(limit=HISTORY_LIMIT):
    rows = []
    logs = AuditLog.objects.filter(object_type="inventory.product", action=AuditLog.ACTION_UPDATE).order_by("-id")[:300]
    for log in logs:
        changes = (log.metadata or {}).get("changes", {})
        if not PRICE_FIELDS & set(changes):
            continue
        after = log.after or {}
        rows.append({
            "product_id": int(log.object_id) if log.object_id.isdigit() else None,
            "label": log.object_label.rsplit(" (", 1)[0],
            "selling_price": after.get("selling_price"),
            "pack_selling_price": after.get("pack_selling_price"),
            "units_per_pack": after.get("units_per_pack"),
            "by": log.actor_name or "System",
            "at": timezone.localtime(log.created_at).strftime("%d %b %H:%M"),
        })
        if len(rows) >= limit:
            break
    return rows


@permission_required("change_selling_price")
def price_entry(request):
    base = Product.objects.filter(is_active=True).select_related("category")
    missing = base.filter(selling_price=0).order_by("id")
    total_missing = missing.count()
    queue = [_serialize(p, request.user) for p in missing.with_stock()[:QUEUE_LIMIT]]
    return render(request, "inventory/price_entry.html", {
        "queue": queue,
        "history": _history(),
        "total_missing": total_missing,
        "total_active": base.count(),
    })


@api_permission_required("change_selling_price")
@require_GET
def price_entry_search(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})
    products = (
        Product.objects.filter(is_active=True).select_related("category")
        .filter(Q(name__icontains=query) | Q(barcode__icontains=query) | Q(variant__icontains=query))
        .with_stock().order_by("name")[:15]
    )
    return JsonResponse({"results": [_serialize(p, request.user) for p in products]})


def _parse_price(raw, label):
    raw = str(raw or "").strip()
    if not raw:
        return None, None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None, f"{label} must be a number."
    if not value.is_finite() or value < 0 or value > MAX_PRICE:
        return None, f"{label} is out of range."
    return value.quantize(Decimal("0.01")), None


@api_permission_required("change_selling_price")
@require_POST
def price_entry_save(request):
    unit, error = _parse_price(request.POST.get("selling_price"), "Unit price")
    if not error and (unit is None or unit <= 0):
        error = "Enter a unit price greater than zero."
    pack = None
    if not error:
        pack, error = _parse_price(request.POST.get("pack_selling_price"), "Pack price")
    units_per_pack = None
    if not error:
        raw_units = str(request.POST.get("units_per_pack", "")).strip()
        if raw_units:
            if not raw_units.isdigit() or int(raw_units) < 2 or int(raw_units) > 100000:
                error = "Units per pack must be a whole number of 2 or more."
            else:
                units_per_pack = int(raw_units)
    if not error and pack is not None and pack > 0 and not units_per_pack:
        # Allow it only if the product already has a pack size recorded.
        existing = Product.objects.filter(pk=request.POST.get("product_id") or 0).values_list("units_per_pack", flat=True).first()
        if not existing:
            error = "Enter how many units are in a pack to set a pack price."
    if error:
        return JsonResponse({"error": error}, status=400)
    if pack is not None and pack == 0:
        pack = None

    with transaction.atomic():
        product = Product.objects.select_for_update().filter(pk=request.POST.get("product_id") or 0).first()
        if product is None:
            return JsonResponse({"error": "That product no longer exists."}, status=404)
        product.selling_price = unit
        if units_per_pack:
            product.units_per_pack = units_per_pack
        product.pack_selling_price = pack if product.units_per_pack else None
        product.save()

    product = Product.objects.select_related("category").with_stock().get(pk=product.pk)
    history = _history(limit=1)
    return JsonResponse({"product": _serialize(product, request.user), "history": history[0] if history else None})
