from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access_control import has_access, permission_required
from .audit_signals import log_event
from .models import (
    AuditLog,
    Category,
    Product,
    StockMovement,
    StockReceipt,
    StockReceiptLine,
)

MAX_RECEIPT_QUANTITY = 999_999


def _can_view_receipts(user):
    return has_access(user, "receive_stock") or has_access(user, "approve_stock_receipts")


def _can_edit_receipt(user, receipt):
    if receipt.status == StockReceipt.STATUS_DRAFT:
        return has_access(user, "receive_stock")
    if receipt.status == StockReceipt.STATUS_SUBMITTED:
        return has_access(user, "approve_stock_receipts")
    return False


def _unique_internal_barcode():
    while True:
        code = f"MANUAL-{uuid4().hex[:12].upper()}"
        if not Product.objects.filter(barcode=code).exists():
            return code


def _parse_quantity(value):
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None
    if quantity < 1 or quantity > MAX_RECEIPT_QUANTITY:
        return None
    return quantity


@login_required
def receipt_list(request):
    if not _can_view_receipts(request.user):
        messages.error(request, "Access denied. Your account cannot view stock receipts.")
        return redirect("dashboard")

    receipts = (
        StockReceipt.objects
        .select_related("created_by", "submitted_by", "applied_by")
        .prefetch_related("lines")
    )
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()

    if query:
        receipts = receipts.filter(
            Q(reference__icontains=query)
            | Q(supplier__icontains=query)
            | Q(notes__icontains=query)
            | Q(created_by__username__icontains=query)
            | Q(lines__product__name__icontains=query)
            | Q(lines__product__barcode__icontains=query)
        ).distinct()
    if status in dict(StockReceipt.STATUS_CHOICES):
        receipts = receipts.filter(status=status)

    return render(request, "inventory/stock_receipt_list.html", {
        "receipts": receipts,
        "query": query,
        "selected_status": status,
        "status_choices": StockReceipt.STATUS_CHOICES,
        "can_create": has_access(request.user, "receive_stock"),
        "can_apply": has_access(request.user, "approve_stock_receipts"),
    })


@permission_required("receive_stock")
def receipt_create(request):
    if request.method == "POST":
        receipt = StockReceipt.objects.create(
            supplier=request.POST.get("supplier", "").strip()[:180],
            reference=request.POST.get("reference", "").strip()[:120],
            notes=request.POST.get("notes", "").strip(),
            created_by=request.user,
        )
        log_event(
            AuditLog.ACTION_CREATE,
            f"Stock receipt #{receipt.pk} created.",
            instance=receipt,
            actor=request.user,
            metadata={"supplier": receipt.supplier, "reference": receipt.reference},
        )
        messages.success(request, "Stock receipt started. Add the delivered products, then submit it for approval.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    return render(request, "inventory/stock_receipt_create.html")


@login_required
def receipt_detail(request, receipt_id):
    if not _can_view_receipts(request.user):
        messages.error(request, "Access denied. Your account cannot view stock receipts.")
        return redirect("dashboard")

    receipt = get_object_or_404(
        StockReceipt.objects.select_related(
            "created_by", "submitted_by", "applied_by", "cancelled_by"
        ),
        pk=receipt_id,
    )
    lines = receipt.lines.select_related("product", "product__category", "entered_by")
    return render(request, "inventory/stock_receipt_detail.html", {
        "receipt": receipt,
        "lines": lines,
        "can_edit": _can_edit_receipt(request.user, receipt),
        "can_submit": receipt.status == StockReceipt.STATUS_DRAFT and has_access(request.user, "receive_stock"),
        "can_apply": receipt.status == StockReceipt.STATUS_SUBMITTED and has_access(request.user, "approve_stock_receipts"),
        "can_cancel": (
            receipt.status == StockReceipt.STATUS_DRAFT and has_access(request.user, "receive_stock")
        ) or (
            receipt.status == StockReceipt.STATUS_SUBMITTED and has_access(request.user, "approve_stock_receipts")
        ),
        "can_create_products": has_access(request.user, "create_products"),
        "can_view_cost": has_access(request.user, "view_cost_price") or has_access(request.user, "change_cost_price"),
        "can_change_cost": has_access(request.user, "change_cost_price"),
        "max_receipt_quantity": MAX_RECEIPT_QUANTITY,
    })


@permission_required("receive_stock")
def receipt_product_search(request):
    query = request.GET.get("q", "").strip()
    if not query:
        return JsonResponse({"results": []})

    products = (
        Product.objects.filter(is_active=True)
        .select_related("category")
        .filter(
            Q(name__icontains=query)
            | Q(barcode__icontains=query)
            | Q(variant__icontains=query)
            | Q(category__name__icontains=query)
        )
        .order_by("name")[:12]
    )

    results = []
    can_view_cost = has_access(request.user, "view_cost_price") or has_access(request.user, "change_cost_price")
    for product in products:
        row = {
            "id": product.pk,
            "name": product.name,
            "barcode": product.barcode,
            "barcode_display": "No barcode" if product.barcode.startswith("MANUAL-") else product.barcode,
            "variant": product.variant or "",
            "category": product.category.name if product.category else "",
            "selling_price": str(product.selling_price),
            "reorder_level": product.reorder_level,
            "stock": product.stock_on_hand,
        }
        if can_view_cost:
            row["cost_price"] = str(product.cost_price)
        results.append(row)
    return JsonResponse({"results": results})


@permission_required("receive_stock")
@require_POST
@transaction.atomic
def receipt_add_line(request, receipt_id):
    receipt = get_object_or_404(StockReceipt.objects.select_for_update(), pk=receipt_id)
    if receipt.status != StockReceipt.STATUS_DRAFT:
        messages.error(request, "Only draft receipts can accept new lines.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    quantity = _parse_quantity(request.POST.get("quantity"))
    if quantity is None:
        messages.error(
            request,
            f"Quantity must be a whole number from 1 to {MAX_RECEIPT_QUANTITY:,}. "
            "Very large values are blocked because they are often scanned barcodes.",
        )
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    product = None
    product_id = request.POST.get("product_id", "").strip()
    barcode = request.POST.get("barcode", "").strip()

    if product_id:
        product = Product.objects.filter(pk=product_id, is_active=True).first()
    if product is None and barcode:
        product = Product.objects.filter(barcode=barcode, is_active=True).first()

    if product is None:
        if not has_access(request.user, "create_products"):
            messages.error(request, "Product not found. Your account cannot create products.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Choose an existing product or enter a name for the new product.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

        try:
            selling_price = Decimal(request.POST.get("selling_price", "0") or "0")
            cost_price = Decimal(request.POST.get("cost_price", "0") or "0")
            reorder_level = max(0, int(request.POST.get("reorder_level", "5") or 5))
        except (InvalidOperation, TypeError, ValueError):
            messages.error(request, "Enter valid prices and low-stock alert level.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

        if selling_price < 0 or cost_price < 0:
            messages.error(request, "Prices cannot be negative.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

        if not has_access(request.user, "change_cost_price"):
            cost_price = Decimal("0.00")

        if barcode and Product.objects.filter(barcode=barcode).exists():
            messages.error(request, "That barcode already belongs to another product.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

        category = None
        category_name = request.POST.get("category", "").strip()
        if category_name:
            category, _ = Category.objects.get_or_create(name=category_name)

        product = Product.objects.create(
            name=name,
            barcode=barcode or _unique_internal_barcode(),
            variant=request.POST.get("variant", "").strip(),
            category=category,
            cost_price=cost_price,
            selling_price=selling_price,
            reorder_level=reorder_level,
        )

    if receipt.lines.filter(product=product).exists():
        messages.error(request, f"{product.name} is already on this receipt. Edit its existing quantity instead.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    line = StockReceiptLine.objects.create(
        receipt=receipt,
        product=product,
        quantity=quantity,
        note=request.POST.get("note", "").strip()[:240],
        entered_by=request.user,
    )
    log_event(
        AuditLog.ACTION_CREATE,
        f"{product.name} added to stock receipt #{receipt.pk}.",
        instance=receipt,
        actor=request.user,
        metadata={"line_id": line.pk, "product_id": product.pk, "quantity": quantity},
    )
    messages.success(request, f"{product.name}: {quantity} units added to the pending receipt.")
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)


@login_required
@require_POST
def receipt_update_line(request, receipt_id, line_id):
    receipt = get_object_or_404(StockReceipt, pk=receipt_id)
    if not _can_edit_receipt(request.user, receipt):
        messages.error(request, "This receipt is not editable by your account at its current stage.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    line = get_object_or_404(StockReceiptLine.objects.select_related("product"), pk=line_id, receipt=receipt)
    quantity = _parse_quantity(request.POST.get("quantity"))
    if quantity is None:
        messages.error(request, f"Quantity must be between 1 and {MAX_RECEIPT_QUANTITY:,}.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    old_quantity = line.quantity
    line.quantity = quantity
    line.note = request.POST.get("note", line.note).strip()[:240]
    line.entered_by = request.user
    line.save(update_fields=["quantity", "note", "entered_by", "updated_at"])

    log_event(
        AuditLog.ACTION_UPDATE,
        f"{line.product.name} quantity updated on stock receipt #{receipt.pk}.",
        instance=receipt,
        actor=request.user,
        metadata={"line_id": line.pk, "from": old_quantity, "to": quantity},
    )
    messages.success(request, f"{line.product.name} updated to {quantity} units.")
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)


@login_required
@require_POST
def receipt_delete_line(request, receipt_id, line_id):
    receipt = get_object_or_404(StockReceipt, pk=receipt_id)
    if not _can_edit_receipt(request.user, receipt):
        messages.error(request, "This receipt is not editable by your account at its current stage.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    line = get_object_or_404(StockReceiptLine.objects.select_related("product"), pk=line_id, receipt=receipt)
    product_name = line.product.name
    line_id_value = line.pk
    line.delete()
    log_event(
        AuditLog.ACTION_DELETE,
        f"{product_name} removed from stock receipt #{receipt.pk}.",
        instance=receipt,
        actor=request.user,
        metadata={"line_id": line_id_value},
    )
    messages.success(request, f"{product_name} removed from the receipt.")
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)


@permission_required("receive_stock")
@require_POST
def receipt_submit(request, receipt_id):
    receipt = get_object_or_404(StockReceipt, pk=receipt_id)
    if receipt.status != StockReceipt.STATUS_DRAFT:
        messages.error(request, "Only a draft receipt can be submitted.")
    elif not receipt.lines.exists():
        messages.error(request, "Add at least one product before submitting the receipt.")
    else:
        receipt.status = StockReceipt.STATUS_SUBMITTED
        receipt.submitted_by = request.user
        receipt.submitted_at = timezone.now()
        receipt.save(update_fields=["status", "submitted_by", "submitted_at"])
        log_event(
            AuditLog.ACTION_UPDATE,
            f"Stock receipt #{receipt.pk} submitted for approval.",
            instance=receipt,
            actor=request.user,
            metadata={"line_count": receipt.lines.count(), "total_units": receipt.total_units},
        )
        messages.success(request, "Receipt submitted. Live stock has not changed; it is awaiting approval.")
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)


@permission_required("approve_stock_receipts")
@require_POST
@transaction.atomic
def receipt_apply(request, receipt_id):
    receipt = get_object_or_404(
        StockReceipt.objects.select_for_update(),
        pk=receipt_id,
        status=StockReceipt.STATUS_SUBMITTED,
    )
    lines = list(receipt.lines.select_related("product"))
    if not lines:
        messages.error(request, "This receipt has no lines to apply.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    for line in lines:
        details = [f"Stock receipt #{receipt.pk}"]
        if receipt.reference:
            details.append(f"Ref: {receipt.reference}")
        if receipt.supplier:
            details.append(f"Supplier: {receipt.supplier}")
        if line.note:
            details.append(line.note)
        StockMovement.objects.create(
            product=line.product,
            actor=request.user,
            movement_type=StockMovement.RECEIVE,
            quantity=line.quantity,
            note=" | ".join(details)[:240],
        )

    receipt.status = StockReceipt.STATUS_APPLIED
    receipt.applied_by = request.user
    receipt.applied_at = timezone.now()
    receipt.save(update_fields=["status", "applied_by", "applied_at"])
    log_event(
        AuditLog.ACTION_UPDATE,
        f"Stock receipt #{receipt.pk} approved and applied.",
        instance=receipt,
        actor=request.user,
        metadata={"line_count": len(lines), "total_units": receipt.total_units},
    )
    messages.success(request, f"Receipt approved. {receipt.total_units} units were added to live stock.")
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)


@login_required
@require_POST
def receipt_cancel(request, receipt_id):
    receipt = get_object_or_404(StockReceipt, pk=receipt_id)
    allowed = (
        receipt.status == StockReceipt.STATUS_DRAFT and has_access(request.user, "receive_stock")
    ) or (
        receipt.status == StockReceipt.STATUS_SUBMITTED and has_access(request.user, "approve_stock_receipts")
    )
    if not allowed:
        messages.error(request, "You cannot cancel this receipt at its current stage.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    receipt.status = StockReceipt.STATUS_CANCELLED
    receipt.cancelled_by = request.user
    receipt.cancelled_at = timezone.now()
    receipt.save(update_fields=["status", "cancelled_by", "cancelled_at"])
    log_event(
        AuditLog.ACTION_UPDATE,
        f"Stock receipt #{receipt.pk} cancelled.",
        instance=receipt,
        actor=request.user,
    )
    messages.success(request, "Receipt cancelled. No stock was added.")
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)
