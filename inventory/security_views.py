import csv
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .access_control import has_access
from .hardened_forms import AuditedProductForm
from .models import Product, Sale, SaleItem, StockMovement, UserProfile
from .stocktake_models import StocktakeSession
from .views import api_active_catalog as legacy_active_catalog
from .views import api_product_search as legacy_product_search
from .views import role_required


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_CASHIER])
def api_product_search(request):
    return legacy_product_search(request)


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_CASHIER])
def api_active_catalog(request):
    return legacy_active_catalog(request)


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    active_stocktake_count = (
        product.stocktake_counts.select_related("session", "zone")
        .filter(
            session__status__in=[
                StocktakeSession.STATUS_DRAFT,
                StocktakeSession.STATUS_COUNTING,
                StocktakeSession.STATUS_REVIEW,
            ]
        )
        .order_by("-session__created_at", "zone__name")
        .first()
    )
    before_stock = product.stock_on_hand
    form = AuditedProductForm(request.POST or None, request.FILES or None, instance=product, user=request.user)
    if active_stocktake_count and "total_stock" in form.fields:
        form.fields["total_stock"].disabled = True
        form.fields["total_stock"].help_text = (
            "Quantity is controlled by the active stocktake. Edit the counted quantity "
            "inside the stocktake so the pending count and live stock do not diverge."
        )
    if request.method == "POST" and form.is_valid():
        product = form.save()
        after_stock = product.stock_on_hand
        if after_stock != before_stock:
            actor = request.user.get_full_name() or request.user.username
            movement = StockMovement.objects.filter(
                product=product,
                movement_type=StockMovement.ADJUSTMENT,
            ).order_by("-id").first()
            if movement:
                movement.note = f"{movement.note} | By: {actor}"[:240]
                movement.save(update_fields=["note"])
        messages.success(request, f"Product '{product.name}' updated.")
        return redirect("product_list")
    return render(
        request,
        "inventory/product_form.html",
        {
            "form": form,
            "title": f"Edit Product: {product.name}",
            "active_stocktake_count": active_stocktake_count,
        },
    )


@require_POST
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)

    blockers = []
    if product.movements.exists():
        blockers.append("stock movement history")
    if product.saleitem_set.exists():
        blockers.append("sales history")
    if product.stocktake_counts.exists():
        blockers.append("stocktake history")
    if product.receipt_lines.exists():
        blockers.append("stock receipt history")

    if blockers:
        messages.error(
            request,
            f"'{product.name}' cannot be permanently deleted because it has "
            + ", ".join(blockers)
            + ". Deactivate it instead so the audit trail remains intact.",
        )
        return redirect("product_list")

    name = product.name
    product.delete()
    messages.success(request, f"Unused product '{name}' permanently deleted.")
    return redirect("product_list")


@role_required([UserProfile.ROLE_ADMIN])
@require_POST
def trigger_manual_backup(request):
    from .backup_utils import run_backup_job
    success, message = run_backup_job()
    messages.success(request, message) if success else messages.error(request, message)
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
        "top_products": completed_items.values("product__name").annotate(
            quantity=Sum("quantity"), sales=Sum("line_total"), rows=Count("id")
        ).order_by("-quantity")[:10],
    }
    return render(request, "inventory/reports.html", context)


@role_required([UserProfile.ROLE_ADMIN])
def export_sales_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="sales_audit.csv"'
    writer = csv.writer(response)
    writer.writerow(["Sale ID", "Receipt", "Date", "Cashier", "Customer", "Status", "Counts Toward Net Sales", "Total", "Amount Paid", "Change"])
    for sale in Sale.objects.select_related("customer").all():
        writer.writerow([sale.pk, sale.receipt_number or "", sale.created_at, sale.cashier_name,
                         sale.customer.name if sale.customer else "", sale.status,
                         "YES" if sale.status == Sale.STATUS_COMPLETED else "NO",
                         sale.total, sale.amount_paid, sale.change_due])
    return response


def _can_revert_sale(user):
    """Require the 'reverse completed sales' permission.

    Staff accounts carry an administrator profile role for historical reasons,
    so the role must never be used to authorise a reversal. GranularPermission-
    Middleware checks the same permission before this view runs.
    """
    return user.is_authenticated and has_access(user, "reverse_sale")


@require_POST
@transaction.atomic
def sale_revert(request, sale_id):
    if not _can_revert_sale(request.user):
        messages.error(request, "Your account is not allowed to reverse sales.")
        return redirect("pos")

    sale = get_object_or_404(Sale.objects.select_for_update(), pk=sale_id)
    if sale.status == Sale.STATUS_REVERTED:
        messages.error(request, f"Sale #{sale.pk} has already been reverted.")
        return redirect("sale_receipt", sale_id=sale.pk)

    reason = request.POST.get("reversal_reason", "").strip()
    if len(reason) < 5:
        messages.error(request, "Enter a clear reversal reason of at least 5 characters.")
        return redirect("sale_receipt", sale_id=sale.pk)

    actor = request.user.get_full_name() or request.user.username
    for item in sale.items.select_related("product").all():
        StockMovement.objects.create(
            product=item.product,
            movement_type=StockMovement.RETURN,
            quantity=item.quantity,
            actor=request.user,
            note=f"Revert Sale #{sale.pk} by {actor}. Reason: {reason}"[:240],
        )
    sale.status = Sale.STATUS_REVERTED
    sale.save(update_fields=["status"])
    messages.success(request, f"Sale #{sale.pk} reverted by {actor}. Stock levels restored.")
    return redirect("sale_receipt", sale_id=sale.pk)
