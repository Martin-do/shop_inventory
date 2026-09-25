from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from .audit_signals import log_event
from .models import AuditLog, Category, Product, UserProfile
from .stocktake_models import StocktakeCount, StocktakeZone
from .stocktake_views import _can_access_zone, _event, stocktake_save_count as legacy_save_count
from .views import role_required


def _generate_internal_barcode():
    """Return a unique internal stock code for products that have no physical barcode."""
    while True:
        code = f"MANUAL-{uuid4().hex[:12].upper()}"
        if not Product.objects.filter(barcode=code).exists():
            return code


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
@require_POST
def stocktake_save_count(request, zone_id):
    """Support product-only lookup without creating a zero-quantity count."""
    if request.POST.get("lookup_only") != "1":
        return legacy_save_count(request, zone_id)

    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_access_zone(request.user, zone) or not zone.session.can_count or zone.is_complete:
        return JsonResponse({"error": "This zone is not available for counting."}, status=403)

    barcode = request.POST.get("barcode", "").strip()
    if not barcode:
        return JsonResponse({"error": "Enter or scan a barcode first."}, status=400)

    product = Product.objects.filter(barcode=barcode, is_active=True).first()
    if not product:
        return JsonResponse({"unknown": True, "barcode": barcode}, status=404)

    existing_count = StocktakeCount.objects.filter(
        session=zone.session,
        zone=zone,
        product=product,
    ).first()

    return JsonResponse({
        "ok": True,
        "lookup": True,
        "product": product.name,
        "variant": product.variant,
        "barcode": product.barcode,
        "category": product.category.name if product.category else "",
        "selling_price": str(product.selling_price),
        "cost_price": str(product.cost_price),
        "reorder_level": product.reorder_level,
        "existing_count": {
            "id": existing_count.pk,
            "good_quantity": existing_count.good_quantity,
            "damaged_quantity": existing_count.damaged_quantity,
            "expired_quantity": existing_count.expired_quantity,
            "reserved_quantity": existing_count.reserved_quantity,
            "note": existing_count.note,
            "status": existing_count.status,
        } if existing_count else None,
    })


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
@require_POST
@transaction.atomic
def stocktake_quick_product(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_access_zone(request.user, zone) or not zone.session.can_count or zone.is_complete:
        return JsonResponse({"error": "Not permitted."}, status=403)

    supplied_barcode = request.POST.get("barcode", "").strip()
    name = request.POST.get("name", "").strip()
    if not name:
        return JsonResponse({"error": "Product name is required."}, status=400)

    barcode = supplied_barcode or _generate_internal_barcode()
    if supplied_barcode and Product.objects.filter(barcode=barcode).exists():
        return JsonResponse({"error": "That barcode already belongs to a product. Use the barcode lookup instead."}, status=409)

    try:
        selling_price = Decimal(request.POST.get("selling_price", "0") or "0")
        cost_price = Decimal(request.POST.get("cost_price", "0") or "0")
        reorder_level = max(0, int(request.POST.get("reorder_level", "5") or 5))
    except (InvalidOperation, TypeError, ValueError):
        return JsonResponse({"error": "Enter valid prices and reorder level."}, status=400)

    if selling_price < 0 or cost_price < 0:
        return JsonResponse({"error": "Prices cannot be negative."}, status=400)

    try:
        quantities = {
            key: max(0, int(request.POST.get(key, 0) or 0))
            for key in ("good_quantity", "damaged_quantity", "expired_quantity", "reserved_quantity")
        }
    except (TypeError, ValueError):
        return JsonResponse({"error": "Stock quantities must be whole numbers."}, status=400)

    category_name = request.POST.get("category", "").strip()
    category = None
    if category_name:
        category, _ = Category.objects.get_or_create(name=category_name)

    product = Product.objects.create(
        name=name,
        barcode=barcode,
        variant=request.POST.get("variant", "").strip(),
        selling_price=selling_price,
        cost_price=cost_price,
        reorder_level=reorder_level,
        category=category,
    )
    count = StocktakeCount.objects.create(
        session=zone.session,
        zone=zone,
        product=product,
        good_quantity=quantities["good_quantity"],
        damaged_quantity=quantities["damaged_quantity"],
        expired_quantity=quantities["expired_quantity"],
        reserved_quantity=quantities["reserved_quantity"],
        approved_good_quantity=quantities["good_quantity"],
        counted_by=request.user,
        note=request.POST.get("note", "").strip()[:240],
        status=StocktakeCount.STATUS_COUNTED,
    )
    _event(
        zone.session,
        request.user,
        "product_created",
        f"{product.name} created during opening stocktake.",
        {
            "product_id": product.pk,
            "barcode": supplied_barcode,
            "internal_code": barcode if not supplied_barcode else "",
        },
    )
    _event(
        zone.session,
        request.user,
        "count_saved",
        f"Opening count saved for {product.name} in {zone.name}.",
        {"count_id": count.pk, "created": True, **quantities},
    )
    log_event(
        AuditLog.ACTION_CREATE,
        f"Product '{product.name}' created from opening stocktake.",
        instance=product,
        metadata={
            "stocktake_session_id": zone.session_id,
            "zone_id": zone.pk,
            "count_id": count.pk,
            **quantities,
        },
        actor=request.user,
    )
    return JsonResponse({
        "ok": True,
        "product": product.name,
        "variant": product.variant,
        "barcode": barcode,
        "barcode_display": supplied_barcode or "No barcode",
        "has_barcode": bool(supplied_barcode),
        "category": category.name if category else "",
        "count_id": count.pk,
        "quantities": quantities,
    })
