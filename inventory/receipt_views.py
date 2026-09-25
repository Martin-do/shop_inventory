from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access_control import has_access, permission_required
from .models import Category, Product, StockMovement, StockReceipt, StockReceiptLine
from .stocktake_models import StocktakeSession


def _can_open_receipts(user):
    return any(
        has_access(user, codename)
        for codename in ("receive_stock", "review_stock_receipts", "apply_stock_receipts")
    )


@permission_required("receive_stock")
def receipt_list(request):
    receipts = StockReceipt.objects.select_related(
        "created_by", "submitted_by", "applied_by"
    ).prefetch_related("lines")
    return render(request, "inventory/receipt_list.html", {"receipts": receipts})


@permission_required("receive_stock")
def receipt_create(request):
    if request.method == "POST":
        reference = request.POST.get("reference", "").strip()
        if not reference:
            messages.error(request, "Enter a delivery or receipt reference.")
        else:
            receipt = StockReceipt.objects.create(
                reference=reference,
                supplier=request.POST.get("supplier", "").strip(),
                invoice_reference=request.POST.get("invoice_reference", "").strip(),
                notes=request.POST.get("notes", "").strip(),
                created_by=request.user,
            )
            messages.success(request, "Stock receipt created. Add the products that were delivered.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    return render(request, "inventory/receipt_create.html")


@permission_required("receive_stock")
def receipt_detail(request, receipt_id):
    receipt = get_object_or_404(
        StockReceipt.objects.select_related(
            "created_by", "submitted_by", "applied_by"
        ),
        pk=receipt_id,
    )
    lines = receipt.lines.select_related("product", "entered_by", "approved_by").all()
    unapproved_count = lines.exclude(status=StockReceiptLine.STATUS_APPROVED).count()

    active_stocktake_products = set()
    if receipt.status == StockReceipt.STATUS_SUBMITTED:
        active_stocktake_products = set(
            receipt.lines.filter(
                product__stocktake_counts__session__status__in=[
                    StocktakeSession.STATUS_DRAFT,
                    StocktakeSession.STATUS_COUNTING,
                    StocktakeSession.STATUS_REVIEW,
                ]
            ).values_list("product_id", flat=True)
        )

    return render(
        request,
        "inventory/receipt_detail.html",
        {
            "receipt": receipt,
            "lines": lines,
            "unapproved_count": unapproved_count,
            "can_review": has_access(request.user, "review_stock_receipts"),
            "can_apply": has_access(request.user, "apply_stock_receipts"),
            "can_create_products": has_access(request.user, "create_products"),
            "active_stocktake_products": active_stocktake_products,
        },
    )


@permission_required("receive_stock")
@require_POST
@transaction.atomic
def receipt_add_line(request, receipt_id):
    receipt = get_object_or_404(StockReceipt.objects.select_for_update(), pk=receipt_id)
    if receipt.status != StockReceipt.STATUS_DRAFT:
        messages.error(request, "This receipt has already been submitted and can no longer be edited.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    barcode = request.POST.get("barcode", "").strip()
    quantity_raw = request.POST.get("quantity", "").strip()
    try:
        quantity = int(quantity_raw)
    except (TypeError, ValueError):
        quantity = 0
    if quantity < 1:
        messages.error(request, "Quantity received must be at least 1.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    product = Product.objects.filter(barcode=barcode, is_active=True).first() if barcode else None

    if product is None:
        if not has_access(request.user, "create_products"):
            messages.error(request, "Product not found. Your account cannot create new products.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "For a new product, enter the product name.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

        if not barcode:
            barcode = f"MANUAL-{uuid4().hex[:12].upper()}"
        elif Product.objects.filter(barcode=barcode).exists():
            messages.error(request, "That barcode belongs to an existing inactive product. Reactivate or edit that product instead.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

        try:
            selling_price = Decimal(request.POST.get("selling_price", "0") or "0")
            cost_price = Decimal(request.POST.get("cost_price", "0") or "0")
        except InvalidOperation:
            messages.error(request, "Enter valid product prices.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

        if selling_price < 0 or cost_price < 0:
            messages.error(request, "Product prices cannot be negative.")
            return redirect("stock_receipt_detail", receipt_id=receipt.pk)

        category = None
        category_name = request.POST.get("category", "").strip()
        if category_name:
            category, _ = Category.objects.get_or_create(name=category_name)

        product = Product.objects.create(
            name=name,
            barcode=barcode,
            variant=request.POST.get("variant", "").strip(),
            category=category,
            cost_price=cost_price if has_access(request.user, "change_cost_price") else Decimal("0.00"),
            selling_price=selling_price,
            reorder_level=max(0, int(request.POST.get("reorder_level", "5") or "5")),
        )

    line, created = StockReceiptLine.objects.get_or_create(
        receipt=receipt,
        product=product,
        defaults={
            "quantity_received": quantity,
            "note": request.POST.get("note", "").strip()[:240],
            "entered_by": request.user,
        },
    )
    if not created:
        line.quantity_received += quantity
        note = request.POST.get("note", "").strip()
        if note:
            line.note = note[:240]
        line.status = StockReceiptLine.STATUS_PENDING
        line.approved_quantity = None
        line.approved_by = None
        line.approved_at = None
        line.entered_by = request.user
        line.save(update_fields=[
            "quantity_received", "note", "status", "approved_quantity",
            "approved_by", "approved_at", "entered_by", "updated_at",
        ])

    messages.success(
        request,
        f"{product.name}: {'added' if created else 'updated'} to {line.quantity_received} units pending approval.",
    )
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)


@permission_required("receive_stock")
@require_POST
def receipt_remove_line(request, receipt_id, line_id):
    receipt = get_object_or_404(StockReceipt, pk=receipt_id)
    if receipt.status != StockReceipt.STATUS_DRAFT:
        messages.error(request, "Submitted receipts cannot be edited.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    line = get_object_or_404(StockReceiptLine, pk=line_id, receipt=receipt)
    name = line.product.name
    line.delete()
    messages.success(request, f"{name} removed from the draft receipt.")
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)


@permission_required("receive_stock")
@require_POST
@transaction.atomic
def receipt_submit(request, receipt_id):
    receipt = get_object_or_404(StockReceipt.objects.select_for_update(), pk=receipt_id)
    if receipt.status != StockReceipt.STATUS_DRAFT:
        messages.error(request, "Only draft receipts can be submitted.")
    elif not receipt.lines.exists():
        messages.error(request, "Add at least one delivered product before submitting.")
    else:
        receipt.lines.update(
            status=StockReceiptLine.STATUS_PENDING,
            approved_quantity=None,
            approved_by=None,
            approved_at=None,
        )
        receipt.status = StockReceipt.STATUS_SUBMITTED
        receipt.submitted_by = request.user
        receipt.submitted_at = timezone.now()
        receipt.save(update_fields=["status", "submitted_by", "submitted_at"])
        messages.success(request, "Receipt submitted. Live stock has not changed; it is awaiting approval.")
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)


@permission_required("review_stock_receipts")
@require_POST
def receipt_review_line(request, receipt_id, line_id):
    receipt = get_object_or_404(StockReceipt, pk=receipt_id, status=StockReceipt.STATUS_SUBMITTED)
    line = get_object_or_404(StockReceiptLine, pk=line_id, receipt=receipt)

    try:
        approved = int(request.POST.get("approved_quantity", line.quantity_received))
    except (TypeError, ValueError):
        approved = -1

    if approved < 0:
        messages.error(request, "Approved quantity cannot be negative.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    line.approved_quantity = approved
    line.status = StockReceiptLine.STATUS_APPROVED
    line.approved_by = request.user
    line.approved_at = timezone.now()
    line.save(update_fields=["approved_quantity", "status", "approved_by", "approved_at", "updated_at"])
    messages.success(request, f"{line.product.name} approved at {approved} units.")
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)


@permission_required("apply_stock_receipts")
@require_POST
@transaction.atomic
def receipt_apply(request, receipt_id):
    receipt = get_object_or_404(
        StockReceipt.objects.select_for_update(),
        pk=receipt_id,
        status=StockReceipt.STATUS_SUBMITTED,
    )
    lines = list(
        receipt.lines.select_for_update().select_related("product").all()
    )

    if any(line.status != StockReceiptLine.STATUS_APPROVED for line in lines):
        messages.error(request, "Approve every receipt line before applying it.")
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    active_conflicts = [
        line.product.name
        for line in lines
        if line.product.stocktake_counts.filter(
            session__status__in=[
                StocktakeSession.STATUS_DRAFT,
                StocktakeSession.STATUS_COUNTING,
                StocktakeSession.STATUS_REVIEW,
            ]
        ).exists()
    ]
    if active_conflicts:
        names = ", ".join(active_conflicts[:5])
        suffix = "…" if len(active_conflicts) > 5 else ""
        messages.error(
            request,
            f"Cannot apply this receipt while an active stocktake includes: {names}{suffix}. "
            "Keep the receipt pending and apply it after that stocktake is applied or cancelled.",
        )
        return redirect("stock_receipt_detail", receipt_id=receipt.pk)

    movements = 0
    for line in lines:
        quantity = line.approved_quantity or 0
        if quantity <= 0:
            continue
        note_bits = [f"Stock receipt #{receipt.pk}: {receipt.reference}"]
        if receipt.supplier:
            note_bits.append(receipt.supplier)
        if receipt.invoice_reference:
            note_bits.append(f"Invoice {receipt.invoice_reference}")
        StockMovement.objects.create(
            product=line.product,
            actor=request.user,
            movement_type=StockMovement.RECEIVE,
            quantity=quantity,
            note=" | ".join(note_bits)[:240],
        )
        movements += 1

    receipt.status = StockReceipt.STATUS_APPLIED
    receipt.applied_by = request.user
    receipt.applied_at = timezone.now()
    receipt.save(update_fields=["status", "applied_by", "applied_at"])
    messages.success(request, f"Receipt applied. {movements} product stock balances increased.")
    return redirect("stock_receipt_detail", receipt_id=receipt.pk)
