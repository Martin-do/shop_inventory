from decimal import Decimal

from django.contrib import messages
from django.db.models import Count, Sum
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import Sale, SaleItem, UserProfile
from .views import api_active_catalog as legacy_active_catalog
from .views import api_product_search as legacy_product_search
from .views import role_required


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_CASHIER])
def api_product_search(request):
    return legacy_product_search(request)


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_CASHIER])
def api_active_catalog(request):
    return legacy_active_catalog(request)


@role_required([UserProfile.ROLE_ADMIN])
@require_POST
def trigger_manual_backup(request):
    from .backup_utils import run_backup_job

    success, message = run_backup_job()
    if success:
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect("settings_dashboard")


@role_required([UserProfile.ROLE_ADMIN])
def reports(request):
    completed_sales = Sale.objects.filter(status=Sale.STATUS_COMPLETED)
    completed_items = SaleItem.objects.filter(sale__status=Sale.STATUS_COMPLETED)
    reverted_sales = Sale.objects.filter(status=Sale.STATUS_REVERTED)

    context = {
        "sales_total": completed_sales.aggregate(total=Sum("total"))["total"] or Decimal("0.00"),
        "sales_count": completed_sales.count(),
        "items_sold": completed_items.aggregate(total=Sum("quantity"))["total"] or 0,
        "reverted_sales_count": reverted_sales.count(),
        "reverted_sales_total": reverted_sales.aggregate(total=Sum("total"))["total"] or Decimal("0.00"),
        "top_products": (
            completed_items.values("product__name")
            .annotate(quantity=Sum("quantity"), sales=Sum("line_total"), rows=Count("id"))
            .order_by("-quantity")[:10]
        ),
    }
    return render(request, "inventory/reports.html", context)
