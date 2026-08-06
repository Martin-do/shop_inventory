from django.contrib import messages
from django.contrib.auth.models import User
from django.db.models import Count, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .access_control import has_access
from . import hardened_stocktake_views, stocktake_search, stocktake_views
from .stocktake_models import StocktakeCount, StocktakeSession, StocktakeZone


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
    return session.zones.filter(assigned_users=user).exists()


def _assigned_to_zone(user, zone):
    return zone.assigned_users.filter(pk=user.pk).exists()


def stocktake_list(request):
    sessions = StocktakeSession.objects.annotate(
        zone_count=Count("zones", distinct=True),
        count_count=Count("counts", distinct=True),
    )
    if not _can_view_all(request.user):
        sessions = sessions.filter(zones__assigned_users=request.user).distinct()
    return render(request, "inventory/stocktake_list.html", {"sessions": sessions})


def stocktake_detail(request, session_id):
    session = get_object_or_404(StocktakeSession, pk=session_id)
    can_view_all = _can_view_all(request.user)
    if not can_view_all and not _assigned_to_session(request.user, session):
        messages.error(request, "You are not assigned to this stocktake.")
        return redirect("stocktake_list")

    zones = session.zones.prefetch_related("assigned_users").annotate(item_count=Count("counts"))
    if not can_view_all:
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


def stocktake_count_zone(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_view_all(request.user) and not _assigned_to_zone(request.user, zone):
        messages.error(request, "You are not assigned to this zone.")
        return redirect("stocktake_list")
    return stocktake_views.stocktake_count_zone.__wrapped__(request, zone_id)


def stocktake_save_count(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_view_all(request.user) and not _assigned_to_zone(request.user, zone):
        return JsonResponse({"error": "You are not assigned to this zone."}, status=403)
    return hardened_stocktake_views.stocktake_save_count.__wrapped__(request, zone_id)


def stocktake_quick_product(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_view_all(request.user) and not _assigned_to_zone(request.user, zone):
        return JsonResponse({"error": "You are not assigned to this zone."}, status=403)
    return hardened_stocktake_views.stocktake_quick_product.__wrapped__(request, zone_id)


def stocktake_complete_zone(request, zone_id):
    zone = get_object_or_404(StocktakeZone.objects.select_related("session"), pk=zone_id)
    if not _can_view_all(request.user) and not _assigned_to_zone(request.user, zone):
        messages.error(request, "You are not assigned to this zone.")
        return redirect("stocktake_list")
    return stocktake_views.stocktake_complete_zone.__wrapped__(request, zone_id)


def stocktake_product_search(request):
    return stocktake_search.stocktake_product_search.__wrapped__(request)


# The middleware enforces the action-specific granular permission before these run.
stocktake_create = stocktake_views.stocktake_create.__wrapped__
stocktake_assign_zone = stocktake_views.stocktake_assign_zone.__wrapped__
stocktake_start = stocktake_views.stocktake_start.__wrapped__
stocktake_submit_review = stocktake_views.stocktake_submit_review.__wrapped__
stocktake_review_count = stocktake_views.stocktake_review_count.__wrapped__
stocktake_apply = stocktake_views.stocktake_apply.__wrapped__
