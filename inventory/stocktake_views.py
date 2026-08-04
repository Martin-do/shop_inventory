from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .audit_signals import log_event
from .models import AuditLog, Category, Product, StockMovement, UserProfile
from .stocktake_models import StocktakeCount, StocktakeEvent, StocktakeSession, StocktakeZone
from .views import role_required


def _event(session, actor, event, message, metadata=None):
    StocktakeEvent.objects.create(
        session=session,
        actor=actor,
        event=event,
        message=message,
        metadata=metadata or {},
    )
    log_event(
        AuditLog.ACTION_UPDATE,
        message,
        instance=session,
        metadata={"stocktake_event": event, **(metadata or {})},
        actor=actor,
    )


def _can_access_zone(user, zone):
    return user.role == UserProfile.ROLE_ADMIN or zone.assigned_users.filter(pk=user.pk).exists()


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
def stocktake_list(request):
    sessions = StocktakeSession.objects.annotate(
        zone_count=Count("zones", distinct=True),
        count_count=Count("counts", distinct=True),
    )
    if request.user.role != UserProfile.ROLE_ADMIN:
        sessions = sessions.filter(zones__assigned_users=request.user).distinct()
    return render(request, "inventory/stocktake_list.html", {"sessions": sessions})


@role_required([UserProfile.ROLE_ADMIN])
def stocktake_create(request):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        zones = [z.strip() for z in request.POST.get("zones", "").splitlines() if z.strip()]
        if not name or not zones:
            messages.error(request, "Enter a session name and at least one zone, one per line.")
        else:
            session = StocktakeSession.objects.create(
                name=name,
                notes=request.POST.get("notes", "").strip(),
                blind_count=request.POST.get("blind_count") == "on",
                created_by=request.user,
            )
            for zone_name in dict.fromkeys(zones):
                StocktakeZone.objects.create(session=session, name=zone_name)
            _event(session, request.user, "created", f"Opening stocktake '{session.name}' created.")
            messages.success(request, "Stocktake created. Assign staff to zones before counting.")
            return redirect("stocktake_detail", session_id=session.pk)
    return render(request, "inventory/stocktake_create.html")


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
def stocktake_detail(request, session_id):
    session = get_object_or_404(StocktakeSession, pk=session_id)
    if request.user.role != UserProfile.ROLE_ADMIN and not session.zones.filter(assigned_users=request.user).exists():
        messages.error(request, "You are not assigned to this stocktake.")
        return redirect("stocktake_list")

    zones = session.zones.prefetch_related("assigned_users").annotate(item_count=Count("counts"))
    if request.user.role != UserProfile.ROLE_ADMIN:
        zones = zones.filter(assigned_users=request.user)

    product_totals = (
        session.counts.values("product_id", "product__name", "product__barcode")
        .annotate(
            entered=Sum("good_quantity"),
            approved=Sum("approved_good_quantity"),
            damaged=Sum("damaged_quantity"),
            expired=Sum("expired_quantity"),
        )
        .order_by("product__name")
    )
    context = {
        "session": session,
        "zones": zones,
        "product_totals": product_totals[:100],
        "staff": User.objects.filter(profile__role__in=[UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK], is_active=True).order_by("username"),
        "recount_count": session.counts.filter(status=StocktakeCount.STATUS_RECOUNT).count(),
        "unapproved_count": session.counts.exclude(status=StocktakeCount.STATUS_APPROVED).count(),
    }
    return render(request, "inventory/stocktake_detail.html", context)


@role_required([UserProfile.ROLE_ADMIN])
@require_POST
def stocktake_assign_zone(request, zone_id):
    zone = get_object_or_404(StocktakeZone, pk=zone_id)
    user_ids = request.POST.getlist("users")
    zone.assigned_users.set(User.objects.filter(pk__in=user_ids, is_active=True))
    _event(zone.session, request.user, "zone_assigned", f"Team assignment updated for zone '{zone.name}'.", {"zone_id": zone.pk, "user_ids": user_ids})
    messages.success(request, f"Assigned staff updated for {zone.name}.")
    return redirect("stocktake_detail", session_id=zone.session_id)


@role_required([UserProfile.ROLE_ADMIN])
@require_POST
def stocktake_start(request, session_id):
    session = get_object_or_404(StocktakeSession, pk=session_id, status=StocktakeSession.STATUS_DRAFT)
    if session.zones.filter(assigned_users=None).exists():
        messages.error(request, "Assign at least one staff member to every zone before starting.")
        return redirect("stocktake_detail", session_id=session.pk)
    session.status = StocktakeSession.STATUS_COUNTING
    session.started_at = timezone.now()
    session.save(update_fields=["status", "started_at"])
    _event(session, request.user, "started", f"Opening stocktake '{session.name}' started.")
    return redirect("stocktake_detail", session_id=session.pk)


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
def stocktake_count_zone(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_access_zone(request.user, zone):
        messages.error(request, "You are not assigned to this zone.")
        return redirect("stocktake_list")
    if not zone.session.can_count or zone.is_complete:
        messages.error(request, "This zone is not open for counting.")
        return redirect("stocktake_detail", session_id=zone.session_id)

    recent = zone.counts.select_related("product", "counted_by").order_by("-counted_at")[:30]
    return render(request, "inventory/stocktake_count.html", {"zone": zone, "session": zone.session, "recent": recent})


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
@require_POST
def stocktake_save_count(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_access_zone(request.user, zone) or not zone.session.can_count or zone.is_complete:
        return JsonResponse({"error": "This zone is not available for counting."}, status=403)

    barcode = request.POST.get("barcode", "").strip()
    product = Product.objects.filter(barcode=barcode, is_active=True).first()
    if not product:
        return JsonResponse({"unknown": True, "barcode": barcode}, status=404)

    try:
        values = {
            key: max(0, int(request.POST.get(key, 0) or 0))
            for key in ("good_quantity", "damaged_quantity", "expired_quantity", "reserved_quantity")
        }
    except (TypeError, ValueError):
        return JsonResponse({"error": "Quantities must be whole numbers."}, status=400)

    count, created = StocktakeCount.objects.update_or_create(
        session=zone.session,
        zone=zone,
        product=product,
        defaults={
            **values,
            "approved_good_quantity": values["good_quantity"],
            "counted_by": request.user,
            "note": request.POST.get("note", "").strip()[:240],
            "status": StocktakeCount.STATUS_COUNTED,
        },
    )
    _event(zone.session, request.user, "count_saved", f"Count saved for {product.name} in {zone.name}.", {"count_id": count.pk, "created": created, **values})
    return JsonResponse({
        "ok": True,
        "product": product.name,
        "variant": product.variant,
        "barcode": product.barcode,
        "count_id": count.pk,
        "quantities": values,
    })


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
@require_POST
def stocktake_quick_product(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_access_zone(request.user, zone) or not zone.session.can_count:
        return JsonResponse({"error": "Not permitted."}, status=403)

    barcode = request.POST.get("barcode", "").strip()
    name = request.POST.get("name", "").strip()
    if not barcode or not name:
        return JsonResponse({"error": "Product name and barcode are required."}, status=400)
    if Product.objects.filter(barcode=barcode).exists():
        return JsonResponse({"error": "That barcode already exists. Scan it again."}, status=409)

    try:
        selling_price = Decimal(request.POST.get("selling_price", "0") or "0")
        cost_price = Decimal(request.POST.get("cost_price", "0") or "0")
    except InvalidOperation:
        return JsonResponse({"error": "Enter valid prices."}, status=400)

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
        category=category,
    )
    _event(zone.session, request.user, "product_created", f"{product.name} created during opening stocktake.", {"product_id": product.pk, "barcode": barcode})
    return JsonResponse({"ok": True, "product": product.name, "barcode": barcode})


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
@require_POST
def stocktake_complete_zone(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_access_zone(request.user, zone) or not zone.session.can_count:
        messages.error(request, "You cannot complete this zone.")
    elif not zone.counts.exists():
        messages.error(request, "Record at least one count before completing the zone.")
    else:
        zone.is_complete = True
        zone.completed_by = request.user
        zone.completed_at = timezone.now()
        zone.save(update_fields=["is_complete", "completed_by", "completed_at"])
        _event(zone.session, request.user, "zone_completed", f"Zone '{zone.name}' marked complete.")
        messages.success(request, f"{zone.name} completed.")
    return redirect("stocktake_detail", session_id=zone.session_id)


@role_required([UserProfile.ROLE_ADMIN])
@require_POST
def stocktake_submit_review(request, session_id):
    session = get_object_or_404(StocktakeSession, pk=session_id, status=StocktakeSession.STATUS_COUNTING)
    if session.zones.filter(is_complete=False).exists():
        messages.error(request, "Every zone must be completed before review.")
    else:
        session.status = StocktakeSession.STATUS_REVIEW
        session.submitted_at = timezone.now()
        session.save(update_fields=["status", "submitted_at"])
        _event(session, request.user, "submitted", f"Opening stocktake '{session.name}' submitted for review.")
    return redirect("stocktake_detail", session_id=session.pk)


@role_required([UserProfile.ROLE_ADMIN])
@require_POST
def stocktake_review_count(request, count_id):
    count = get_object_or_404(StocktakeCount.objects.select_related("session", "product"), pk=count_id)
    action = request.POST.get("action")
    if action == "recount":
        count.status = StocktakeCount.STATUS_RECOUNT
        count.note = request.POST.get("note", "Recount requested").strip()[:240]
        count.save(update_fields=["status", "note"])
    else:
        try:
            approved = max(0, int(request.POST.get("approved_good_quantity", count.good_quantity)))
        except ValueError:
            approved = count.good_quantity
        count.approved_good_quantity = approved
        count.recount_quantity = approved if count.status == StocktakeCount.STATUS_RECOUNT else count.recount_quantity
        count.status = StocktakeCount.STATUS_APPROVED
        count.approved_by = request.user
        count.approved_at = timezone.now()
        count.save(update_fields=["approved_good_quantity", "recount_quantity", "status", "approved_by", "approved_at"])
    _event(count.session, request.user, "count_reviewed", f"Count reviewed for {count.product.name}.", {"count_id": count.pk, "action": action})
    return redirect("stocktake_detail", session_id=count.session_id)


@role_required([UserProfile.ROLE_ADMIN])
@require_POST
@transaction.atomic
def stocktake_apply(request, session_id):
    session = get_object_or_404(StocktakeSession.objects.select_for_update(), pk=session_id, status=StocktakeSession.STATUS_REVIEW)
    if session.counts.exclude(status=StocktakeCount.STATUS_APPROVED).exists():
        messages.error(request, "Approve every count before applying opening inventory.")
        return redirect("stocktake_detail", session_id=session.pk)

    totals = defaultdict(int)
    for row in session.counts.values("product_id").annotate(total=Sum("approved_good_quantity")):
        totals[row["product_id"]] = row["total"] or 0

    products = Product.objects.select_for_update().filter(pk__in=totals)
    movements = 0
    for product in products:
        target = totals[product.pk]
        difference = target - product.stock_on_hand
        if difference:
            StockMovement.objects.create(
                product=product,
                actor=request.user,
                movement_type=StockMovement.ADJUSTMENT,
                quantity=difference,
                note=f"Opening inventory: {session.name} (session #{session.pk})"[:240],
            )
            movements += 1

    session.status = StocktakeSession.STATUS_APPLIED
    session.applied_by = request.user
    session.applied_at = timezone.now()
    session.save(update_fields=["status", "applied_by", "applied_at"])
    _event(session, request.user, "applied", f"Opening inventory '{session.name}' applied to {movements} products.", {"products": len(totals), "movements": movements})
    messages.success(request, f"Opening inventory applied. {movements} stock balances changed.")
    return redirect("stocktake_detail", session_id=session.pk)
