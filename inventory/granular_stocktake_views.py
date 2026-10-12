from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .access_control import has_access
from . import hardened_stocktake_views, stocktake_search, stocktake_views
from .stocktake_models import StocktakeCount, StocktakeRemovedCount, StocktakeSession, StocktakeZone


def _can_view_all(user):
    return any(
        has_access(user, codename)
        for codename in (
            "view_all_stocktake_zones",
            "create_stocktakes",
            "assign_stocktake_teams",
            "start_stocktakes",
            "review_stocktake_counts",
            "apply_stocktakes",
        )
    )


def _assigned_to_session(user, session):
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return False
    return session.zones.filter(assigned_users__pk=user.pk).exists()


def _assigned_to_zone(user, zone):
    if not getattr(user, "is_authenticated", False) or not getattr(user, "pk", None):
        return False
    return zone.assigned_users.filter(pk=user.pk).exists()


@login_required
def stocktake_list(request):
    sessions = StocktakeSession.objects.annotate(
        zone_count=Count("zones", distinct=True),
        count_count=Count("counts", distinct=True),
    )
    if not _can_view_all(request.user):
        sessions = sessions.filter(zones__assigned_users__pk=request.user.pk).distinct()
    return render(request, "inventory/stocktake_list.html", {"sessions": sessions})


@login_required
def stocktake_detail(request, session_id):
    session = get_object_or_404(StocktakeSession, pk=session_id)
    can_view_all = _can_view_all(request.user)
    if not can_view_all and not _assigned_to_session(request.user, session):
        messages.error(request, "You are not assigned to this stocktake.")
        return redirect("stocktake_list")

    zones = session.zones.prefetch_related("assigned_users").annotate(item_count=Count("counts"))
    if not can_view_all:
        zones = zones.filter(assigned_users__pk=request.user.pk)

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

    assignable_staff = []
    if has_access(request.user, "assign_stocktake_teams"):
        for user in User.objects.filter(is_active=True).order_by("username"):
            if has_access(user, "count_assigned_zones") or has_access(user, "view_assigned_stocktakes"):
                assignable_staff.append(user)

    return render(
        request,
        "inventory/stocktake_detail.html",
        {
            "session": session,
            "zones": zones,
            "product_totals": product_totals[:100],
            "staff": assignable_staff,
            "recount_count": session.counts.filter(status=StocktakeCount.STATUS_RECOUNT).count(),
            "unapproved_count": session.counts.exclude(status=StocktakeCount.STATUS_APPROVED).count(),
        },
    )


@login_required
def stocktake_count_zone(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_view_all(request.user) and not _assigned_to_zone(request.user, zone):
        messages.error(request, "You are not assigned to this zone.")
        return redirect("stocktake_list")
    return stocktake_views.stocktake_count_zone.__wrapped__(request, zone_id)


@login_required
def stocktake_save_count(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_view_all(request.user) and not _assigned_to_zone(request.user, zone):
        return JsonResponse({"error": "You are not assigned to this zone."}, status=403)
    return hardened_stocktake_views.stocktake_save_count.__wrapped__(request, zone_id)


@login_required
def stocktake_quick_product(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_view_all(request.user) and not _assigned_to_zone(request.user, zone):
        return JsonResponse({"error": "You are not assigned to this zone."}, status=403)
    return hardened_stocktake_views.stocktake_quick_product.__wrapped__(request, zone_id)


def stocktake_edit_record(request, count_id):
    count = get_object_or_404(StocktakeCount.objects.select_related("zone__session"), pk=count_id)
    zone = count.zone
    if not _can_view_all(request.user) and not _assigned_to_zone(request.user, zone):
        return JsonResponse({"error": "You are not assigned to this zone."}, status=403)
    return hardened_stocktake_views.stocktake_edit_record.__wrapped__(request, count_id)


def _can_change_count(user, zone):
    return has_access(user, "count_assigned_zones") and (
        _can_view_all(user) or _assigned_to_zone(user, zone)
    )


@login_required
@require_POST
@transaction.atomic
def stocktake_remove_count(request, count_id):
    count = get_object_or_404(
        StocktakeCount.objects.select_for_update().select_related(
            "session", "zone", "product", "counted_by"
        ), pk=count_id
    )
    if not _can_change_count(request.user, count.zone):
        return JsonResponse({"error": "You cannot remove counts in this zone."}, status=403)
    if not count.session.can_count or count.zone.is_complete:
        return JsonResponse({"error": "Only an active, unfinished zone may have counts removed."}, status=409)

    reason = request.POST.get("reason", "").strip()
    if len(reason) < 5:
        return JsonResponse({"error": "Explain why this count should be removed (at least five characters)."}, status=400)

    snapshot = {
        "good_quantity": count.good_quantity,
        "damaged_quantity": count.damaged_quantity,
        "expired_quantity": count.expired_quantity,
        "reserved_quantity": count.reserved_quantity,
        "approved_good_quantity": count.approved_good_quantity,
        "first_count_quantity": count.first_count_quantity,
        "count_entry_mode": count.count_entry_mode,
        "pack_count_entered": count.pack_count_entered,
        "loose_units_entered": count.loose_units_entered,
        "units_per_pack_snapshot": count.units_per_pack_snapshot,
        "recount_quantity": count.recount_quantity,
        "note": count.note,
        "status": count.status,
        "counted_by_id": count.counted_by_id,
        "counted_at": count.counted_at.isoformat(),
    }
    original_count_id = count.pk
    removed = StocktakeRemovedCount.objects.create(
        session=count.session, zone=count.zone, product=count.product,
        original_count_id=original_count_id, snapshot=snapshot,
        reason=reason[:240], removed_by=request.user,
    )
    name = count.product.name
    zone_name = count.zone.name
    session = count.session
    count.delete()
    stocktake_views._event(
        session, request.user, "count_removed",
        f"Count for {name} removed from {zone_name}.",
        {
            "removed_id": removed.pk,
            "original_count_id": original_count_id,
            "product_id": removed.product_id,
            "zone_id": removed.zone_id,
            "reason": removed.reason,
            "snapshot": snapshot,
        },
    )
    return JsonResponse({"ok": True, "removed_id": removed.pk, "count_id": original_count_id})


@login_required
@require_POST
@transaction.atomic
def stocktake_restore_count(request, removed_id):
    removed = get_object_or_404(
        StocktakeRemovedCount.objects.select_for_update().select_related("session", "zone", "product"),
        pk=removed_id,
    )
    if not _can_change_count(request.user, removed.zone):
        return JsonResponse({"error": "You cannot restore counts in this zone."}, status=403)
    if not removed.session.can_count or removed.zone.is_complete:
        return JsonResponse({"error": "Only counts removed from unfinished stocktakes can be restored."}, status=409)
    if removed.restored_at is not None:
        return JsonResponse({"error": "This removed count has already been restored."}, status=409)

    if StocktakeCount.objects.filter(
        session=removed.session, zone=removed.zone, product=removed.product
    ).exists():
        return JsonResponse({
            "error": "A new count now exists for this product in this zone. Review it before restoring.",
        }, status=409)

    snapshot = removed.snapshot
    # An older saved count is restored as a pending count, never silently approved.
    restored = StocktakeCount.objects.create(
        session=removed.session, zone=removed.zone, product=removed.product,
        good_quantity=snapshot["good_quantity"],
        damaged_quantity=snapshot["damaged_quantity"],
        expired_quantity=snapshot["expired_quantity"],
        reserved_quantity=snapshot["reserved_quantity"],
        approved_good_quantity=snapshot["approved_good_quantity"],
        count_entry_mode=snapshot.get("count_entry_mode", "units"),
        pack_count_entered=snapshot.get("pack_count_entered"),
        loose_units_entered=snapshot.get("loose_units_entered"),
        units_per_pack_snapshot=snapshot.get("units_per_pack_snapshot"),
        recount_quantity=snapshot.get("recount_quantity"),
        note=snapshot.get("note", ""),
        counted_by_id=snapshot["counted_by_id"],
        status=StocktakeCount.STATUS_COUNTED,
    )
    StocktakeCount.objects.filter(pk=restored.pk).update(
        first_count_quantity=snapshot["first_count_quantity"],
    )
    removed.restored_by = request.user
    removed.restored_at = timezone.now()
    removed.restored_count_id = restored.pk
    removed.save(update_fields=["restored_by", "restored_at", "restored_count_id"])
    stocktake_views._event(
        removed.session, request.user, "count_restored",
        f"Count for {removed.product.name} restored in {removed.zone.name}.",
        {
            "removed_id": removed.pk,
            "count_id": restored.pk,
            "original_count_id": removed.original_count_id,
            "product_id": removed.product_id,
            "zone_id": removed.zone_id,
        },
    )
    return JsonResponse({"ok": True, "count_id": restored.pk, "removed_id": removed.pk})


@login_required
def stocktake_complete_zone(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_view_all(request.user) and not _assigned_to_zone(request.user, zone):
        messages.error(request, "You are not assigned to this zone.")
        return redirect("stocktake_list")
    return stocktake_views.stocktake_complete_zone.__wrapped__(request, zone_id)


@login_required
def stocktake_product_search(request):
    return stocktake_search.stocktake_product_search.__wrapped__(request)


# The middleware enforces the action-specific granular permission before these run.
# login_required ensures expired or missing sessions redirect safely before legacy code executes.
stocktake_create = login_required(stocktake_views.stocktake_create.__wrapped__)
stocktake_assign_zone = login_required(stocktake_views.stocktake_assign_zone.__wrapped__)
stocktake_start = login_required(stocktake_views.stocktake_start.__wrapped__)
stocktake_submit_review = login_required(stocktake_views.stocktake_submit_review.__wrapped__)
stocktake_review_count = login_required(stocktake_views.stocktake_review_count.__wrapped__)
stocktake_apply = login_required(stocktake_views.stocktake_apply.__wrapped__)
