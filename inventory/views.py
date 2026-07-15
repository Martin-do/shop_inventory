import csv
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import (
    AddToCartForm, CheckoutForm, ProductForm, ReceiveStockForm,
    StoreSettingsForm, CustomerForm, CategoryForm, StaffForm
)
from .models import Product, Sale, SaleItem, StockMovement, Customer, StoreSettings, Category, BackupLog


def _cart(request):
    return request.session.setdefault("cart", {})


def _cart_lines(cart):
    products = Product.objects.filter(barcode__in=cart.keys(), is_active=True)
    product_map = {product.barcode: product for product in products}
    lines = []
    total = Decimal("0.00")

    for barcode, quantity in cart.items():
        product = product_map.get(barcode)
        if not product:
            continue
        line_total = product.selling_price * quantity
        total += line_total
        lines.append({"product": product, "quantity": quantity, "line_total": line_total})

    return lines, total


@login_required
def dashboard(request):
    if not request.user.is_staff:
        return redirect("pos")
    products = list(Product.objects.with_stock())
    stock_values = [
        product.stock_on_hand * product.selling_price
        for product in products
    ]
    context = {
        "product_count": len(products),
        "low_stock_count": sum(1 for product in products if product.is_low_stock),
        "sale_count": Sale.objects.count(),
        "stock_value": sum(stock_values, Decimal("0.00")),
        "recent_movements": StockMovement.objects.select_related("product")[:8],
    }
    return render(request, "inventory/dashboard.html", context)


@login_required
def product_list(request):
    query = request.GET.get("q", "").strip()
    products = Product.objects.select_related("category").with_stock()
    if query:
        products = products.filter(name__icontains=query) | products.filter(barcode__icontains=query)
    return render(request, "inventory/product_list.html", {"products": products, "query": query})


@login_required
@user_passes_test(lambda u: u.is_staff)
def product_create(request):
    form = ProductForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        product = form.save()
        opening_stock = form.cleaned_data.get("opening_stock") or 0
        if opening_stock:
            StockMovement.objects.create(
                product=product,
                movement_type=StockMovement.RECEIVE,
                quantity=opening_stock,
                note="Opening stock",
            )
        messages.success(request, "Product created.")
        return redirect("product_list")
    return render(request, "inventory/product_form.html", {"form": form, "title": "Add Product"})


@login_required
@user_passes_test(lambda u: u.is_staff)
def product_update(request, pk):
    product = get_object_or_404(Product, pk=pk)
    form = ProductForm(request.POST or None, request.FILES or None, instance=product)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Product updated.")
        return redirect("product_list")
    return render(request, "inventory/product_form.html", {"form": form, "title": "Edit Product"})


@login_required
@user_passes_test(lambda u: u.is_staff)
@require_POST
def product_toggle_active(request, pk):
    product = get_object_or_404(Product, pk=pk)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active", "updated_at"])
    state = "activated" if product.is_active else "deactivated"
    messages.success(request, f"{product.name} {state}.")
    return redirect("product_list")


@login_required
@user_passes_test(lambda u: u.is_staff)
def receive_stock(request):
    product = None
    form = ReceiveStockForm()
    if request.method == "POST":
        form = ReceiveStockForm(request.POST)
        if not form.is_valid():
            return render(request, "inventory/receive_stock.html", {"form": form, "product": product})
        barcode = form.cleaned_data["barcode"]
        quantity = form.cleaned_data["quantity"]
        product = get_object_or_404(Product, barcode=barcode, is_active=True)
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.RECEIVE,
            quantity=quantity,
            note=form.cleaned_data["note"],
        )
        messages.success(request, f"Added {quantity} units to {product.name}.")
        return redirect("receive_stock")
    barcode = request.GET.get("barcode", "").strip()
    if barcode:
        product = Product.objects.filter(barcode=barcode, is_active=True).first()
        form = ReceiveStockForm(initial={"barcode": barcode, "quantity": 1})
    return render(request, "inventory/receive_stock.html", {"form": form, "product": product})


@login_required
def pos(request):
    lines, total = _cart_lines(_cart(request))
    settings = StoreSettings.get_solo()
    customers = Customer.objects.all()
    
    default_tax_rate = Decimal("0.00")
    enable_tax = False
    if settings:
        default_tax_rate = settings.default_tax_rate
        enable_tax = settings.enable_tax

    context = {
        "lines": lines,
        "total": total,
        "add_form": AddToCartForm(),
        "checkout_form": CheckoutForm(initial={"amount_paid": total, "tax_rate": default_tax_rate}),
        "customers": customers,
        "settings": settings,
        "default_tax_rate": default_tax_rate,
        "enable_tax": enable_tax,
    }
    return render(request, "inventory/pos.html", context)


@login_required
@require_POST
def pos_add(request):
    form = AddToCartForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(error for errors in form.errors.values() for error in errors))
        return redirect("pos")
    barcode = form.cleaned_data["barcode"]
    quantity = form.cleaned_data["quantity"]
    product = Product.objects.get(barcode=barcode, is_active=True)
    cart = _cart(request)
    next_quantity = cart.get(barcode, 0) + quantity
    if next_quantity > product.stock_on_hand:
        messages.error(request, f"Only {product.stock_on_hand} units of {product.name} are in stock.")
        return redirect("pos")
    cart[barcode] = next_quantity
    request.session.modified = True
    return redirect("pos")


@login_required
@require_POST
def pos_remove(request, barcode):
    cart = _cart(request)
    if barcode in cart:
        del cart[barcode]
        request.session.modified = True
    return redirect("pos")


@login_required
@require_POST
def pos_clear(request):
    request.session["cart"] = {}
    return redirect("pos")


@login_required
@require_POST
@transaction.atomic
def pos_checkout(request):
    lines, subtotal = _cart_lines(_cart(request))
    if not lines:
        messages.error(request, "Cart is empty.")
        return redirect("pos")

    form = CheckoutForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(error for errors in form.errors.values() for error in errors))
        return redirect("pos")

    # Lock the products in the cart so concurrent checkouts can't oversell the
    # same stock between the availability check and the ledger writes. The lock
    # is taken on the plain product rows (no aggregation, which some databases
    # reject with FOR UPDATE); stock is recomputed from the movement ledger.
    barcodes = [line["product"].barcode for line in lines]
    locked = Product.objects.select_for_update().in_bulk(barcodes, field_name="barcode")

    for line in lines:
        product = locked[line["product"].barcode]
        line["product"] = product
        if line["quantity"] > product.stock_on_hand:
            messages.error(
                request,
                f"Not enough stock for {product.name}. Available: {product.stock_on_hand}.",
            )
            return redirect("pos")

    discount_amount = form.cleaned_data.get("discount_amount") or Decimal("0.00")
    tax_rate = form.cleaned_data.get("tax_rate") or Decimal("0.00")
    customer = form.cleaned_data.get("customer")

    if discount_amount > subtotal:
        messages.error(request, "Discount cannot exceed cart subtotal.")
        return redirect("pos")

    taxable_amount = subtotal - discount_amount
    tax_amount = taxable_amount * (tax_rate / Decimal("100.00"))
    final_total = taxable_amount + tax_amount

    amount_paid = form.cleaned_data["amount_paid"]
    if amount_paid < final_total:
        messages.error(request, f"Amount paid cannot be less than the sale total of {final_total}.")
        return redirect("pos")

    sale = Sale.objects.create(
        cashier_name=form.cleaned_data["cashier_name"],
        customer=customer,
        total=final_total,
        amount_paid=amount_paid,
        discount_amount=discount_amount,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
    )
    for line in lines:
        product = line["product"]
        quantity = line["quantity"]
        SaleItem.objects.create(
            sale=sale,
            product=product,
            quantity=quantity,
            unit_price=product.selling_price,
            line_total=line["line_total"],
        )
        StockMovement.objects.create(
            product=product,
            movement_type=StockMovement.SALE,
            quantity=-quantity,
            note=f"Sale #{sale.pk}",
        )
    request.session["cart"] = {}
    messages.success(request, f"Sale #{sale.pk} completed. Change: {sale.change_due}.")
    return redirect("sale_receipt", sale_id=sale.pk)


@login_required
def sale_detail(request, sale_id):
    sale = get_object_or_404(Sale.objects.prefetch_related("items__product"), pk=sale_id)
    return render(request, "inventory/sale_detail.html", {"sale": sale})


@login_required
@user_passes_test(lambda u: u.is_staff)
def reports(request):
    context = {
        "sales_total": Sale.objects.aggregate(total=Sum("total"))["total"] or Decimal("0.00"),
        "sales_count": Sale.objects.count(),
        "items_sold": SaleItem.objects.aggregate(total=Sum("quantity"))["total"] or 0,
        "top_products": (
            SaleItem.objects.values("product__name")
            .annotate(quantity=Sum("quantity"), sales=Sum("line_total"), rows=Count("id"))
            .order_by("-quantity")[:10]
        ),
    }
    return render(request, "inventory/reports.html", context)


@login_required
@user_passes_test(lambda u: u.is_staff)
def export_products_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="products.csv"'
    writer = csv.writer(response)
    writer.writerow(["Name", "Barcode", "Category", "Cost Price", "Selling Price", "Stock", "Reorder Level"])
    for product in Product.objects.select_related("category").with_stock():
        writer.writerow([
            product.name,
            product.barcode,
            product.category.name if product.category else "",
            product.cost_price,
            product.selling_price,
            product.stock_on_hand,
            product.reorder_level,
        ])
    return response


@login_required
@user_passes_test(lambda u: u.is_staff)
def export_sales_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="sales.csv"'
    writer = csv.writer(response)
    writer.writerow(["Sale ID", "Date", "Cashier", "Total", "Amount Paid", "Change"])
    for sale in Sale.objects.all():
        writer.writerow([sale.pk, sale.created_at, sale.cashier_name, sale.total, sale.amount_paid, sale.change_due])
    return response


@login_required
@user_passes_test(lambda u: u.is_staff)
def settings_dashboard(request):
    settings = StoreSettings.get_solo()
    form = StoreSettingsForm(request.POST or None, request.FILES or None, instance=settings)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Store settings updated.")
        return redirect("settings_dashboard")
    
    backup_logs = BackupLog.objects.all()[:15]
    return render(request, "inventory/settings_dashboard.html", {
        "form": form,
        "settings": settings,
        "backup_logs": backup_logs,
    })


@login_required
@user_passes_test(lambda u: u.is_staff)
def trigger_manual_backup(request):
    from .backup_utils import run_backup_job
    success, message = run_backup_job()
    if success:
        messages.success(request, message)
    else:
        messages.error(request, message)
    return redirect("settings_dashboard")


@login_required
@user_passes_test(lambda u: u.is_staff)
def settings_staff_list(request):
    staff_members = User.objects.all().order_by("-is_staff", "username")
    return render(request, "inventory/settings_staff_list.html", {"staff_members": staff_members})


@login_required
@user_passes_test(lambda u: u.is_staff)
def settings_staff_create(request):
    form = StaffForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Staff account for {user.username} created.")
        return redirect("settings_staff_list")
    return render(request, "inventory/settings_staff_form.html", {"form": form, "title": "Create Staff Account"})


@login_required
@user_passes_test(lambda u: u.is_staff)
def settings_staff_update(request, pk):
    user = get_object_or_404(User, pk=pk)
    form = StaffForm(request.POST or None, instance=user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Staff account for {user.username} updated.")
        return redirect("settings_staff_list")
    return render(request, "inventory/settings_staff_form.html", {"form": form, "title": f"Edit Staff Account: {user.username}"})


@login_required
@user_passes_test(lambda u: u.is_staff)
def settings_category_list(request):
    categories = Category.objects.all().annotate(product_count=Count("product"))
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        category = form.save()
        messages.success(request, f"Category '{category.name}' created.")
        return redirect("settings_category_list")
    return render(request, "inventory/settings_category_list.html", {"categories": categories, "form": form})


@login_required
@user_passes_test(lambda u: u.is_staff)
def settings_category_update(request, pk):
    category = get_object_or_404(Category, pk=pk)
    form = CategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Category '{category.name}' updated.")
        return redirect("settings_category_list")
    return render(request, "inventory/settings_category_form.html", {"form": form, "category": category})


@login_required
@user_passes_test(lambda u: u.is_staff)
@require_POST
def settings_category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if category.product_set.exists():
        messages.error(request, f"Cannot delete category '{category.name}' because it contains products.")
    else:
        category.delete()
        messages.success(request, f"Category '{category.name}' deleted.")
    return redirect("settings_category_list")


@login_required
@user_passes_test(lambda u: u.is_staff)
def customer_list(request):
    query = request.GET.get("q", "").strip()
    customers = Customer.objects.all()
    if query:
        customers = customers.filter(name__icontains=query) | customers.filter(phone__icontains=query)
    return render(request, "inventory/customer_list.html", {"customers": customers, "query": query})


@login_required
@user_passes_test(lambda u: u.is_staff)
def customer_create(request):
    form = CustomerForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        customer = form.save()
        messages.success(request, f"Customer '{customer.name}' created.")
        return redirect("customer_list")
    return render(request, "inventory/customer_form.html", {"form": form, "title": "Add Customer"})


@login_required
@user_passes_test(lambda u: u.is_staff)
def customer_update(request, pk):
    customer = get_object_or_404(Customer, pk=pk)
    form = CustomerForm(request.POST or None, instance=customer)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Customer '{customer.name}' updated.")
        return redirect("customer_list")
    return render(request, "inventory/customer_form.html", {"form": form, "title": "Edit Customer"})


@login_required
def sale_receipt(request, sale_id):
    sale = get_object_or_404(Sale.objects.prefetch_related("items__product"), pk=sale_id)
    settings = StoreSettings.get_solo()
    subtotal = sum(item.line_total for item in sale.items.all())
    return render(request, "inventory/sale_receipt.html", {
        "sale": sale,
        "settings": settings,
        "subtotal": subtotal,
    })


@login_required
@require_POST
@transaction.atomic
def sale_revert(request, sale_id):
    sale = get_object_or_404(Sale, pk=sale_id)
    if sale.status == Sale.STATUS_REVERTED:
        messages.error(request, f"Sale #{sale.pk} has already been reverted.")
        return redirect("sale_receipt", sale_id=sale.pk)
    
    # Return stock to inventory by creating positive RETURN movement
    for item in sale.items.all():
        StockMovement.objects.create(
            product=item.product,
            movement_type=StockMovement.RETURN,
            quantity=item.quantity,
            note=f"Revert of Sale #{sale.pk}",
        )
    
    sale.status = Sale.STATUS_REVERTED
    sale.save(update_fields=["status"])
    
    messages.success(request, f"Sale #{sale.pk} successfully reverted. Stock levels restored.")
    return redirect("sale_receipt", sale_id=sale.pk)


@login_required
def api_product_search(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})
    
    products = Product.objects.filter(is_active=True).with_stock()
    products = products.filter(Q(name__icontains=query) | Q(barcode__icontains=query))[:10]
    
    results = []
    for p in products:
        results.append({
            "name": p.name,
            "barcode": p.barcode,
            "variant": p.variant or "",
            "price": str(p.selling_price),
            "stock": p.stock_on_hand,
            "image_url": p.image.url if p.image else None,
        })
    return JsonResponse({"results": results})
