import csv
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import AddToCartForm, CheckoutForm, ProductForm, ReceiveStockForm
from .models import Product, Sale, SaleItem, StockMovement


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


def dashboard(request):
    products = Product.objects.all()
    stock_values = [
        product.stock_on_hand * product.selling_price
        for product in products
    ]
    context = {
        "product_count": products.count(),
        "low_stock_count": sum(1 for product in products if product.is_low_stock),
        "sale_count": Sale.objects.count(),
        "stock_value": sum(stock_values, Decimal("0.00")),
        "recent_movements": StockMovement.objects.select_related("product")[:8],
    }
    return render(request, "inventory/dashboard.html", context)


def product_list(request):
    query = request.GET.get("q", "").strip()
    products = Product.objects.select_related("category")
    if query:
        products = products.filter(name__icontains=query) | products.filter(barcode__icontains=query)
    return render(request, "inventory/product_list.html", {"products": products, "query": query})


def product_create(request):
    if request.method == "POST":
        form = ProductForm(request.POST)
        if not form.is_valid():
            return render(request, "inventory/product_form.html", {"form": form})
        cleaned = form.cleaned_data
        product = Product.objects.create(
            name=cleaned["name"],
            barcode=cleaned["barcode"],
            category=cleaned["category"],
            cost_price=cleaned["cost_price"] or Decimal("0.00"),
            selling_price=cleaned["selling_price"],
            reorder_level=cleaned["reorder_level"],
        )
        opening_stock = cleaned.get("opening_stock") or 0
        if opening_stock:
            StockMovement.objects.create(
                product=product,
                movement_type=StockMovement.RECEIVE,
                quantity=opening_stock,
                note="Opening stock",
            )
        messages.success(request, "Product created.")
        return redirect("product_list")
    return render(request, "inventory/product_form.html", {"form": ProductForm()})


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


def pos(request):
    lines, total = _cart_lines(_cart(request))
    return render(
        request,
        "inventory/pos.html",
        {"lines": lines, "total": total, "add_form": AddToCartForm(), "checkout_form": CheckoutForm(initial={"amount_paid": total})},
    )


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


@require_POST
def pos_remove(request, barcode):
    cart = _cart(request)
    if barcode in cart:
        del cart[barcode]
        request.session.modified = True
    return redirect("pos")


@require_POST
def pos_clear(request):
    request.session["cart"] = {}
    return redirect("pos")


@require_POST
@transaction.atomic
def pos_checkout(request):
    lines, total = _cart_lines(_cart(request))
    if not lines:
        messages.error(request, "Cart is empty.")
        return redirect("pos")

    form = CheckoutForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(error for errors in form.errors.values() for error in errors))
        return redirect("pos")

    for line in lines:
        if line["quantity"] > line["product"].stock_on_hand:
            messages.error(
                request,
                f"Not enough stock for {line['product'].name}. Available: {line['product'].stock_on_hand}.",
            )
            return redirect("pos")

    amount_paid = form.cleaned_data["amount_paid"]
    if amount_paid < total:
        messages.error(request, "Amount paid cannot be less than the sale total.")
        return redirect("pos")

    sale = Sale.objects.create(
        cashier_name=form.cleaned_data["cashier_name"],
        total=total,
        amount_paid=amount_paid,
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
    return redirect("sale_detail", sale_id=sale.pk)


def sale_detail(request, sale_id):
    sale = get_object_or_404(Sale.objects.prefetch_related("items__product"), pk=sale_id)
    return render(request, "inventory/sale_detail.html", {"sale": sale})


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


def export_products_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="products.csv"'
    writer = csv.writer(response)
    writer.writerow(["Name", "Barcode", "Category", "Cost Price", "Selling Price", "Stock", "Reorder Level"])
    for product in Product.objects.select_related("category"):
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


def export_sales_csv(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="sales.csv"'
    writer = csv.writer(response)
    writer.writerow(["Sale ID", "Date", "Cashier", "Total", "Amount Paid", "Change"])
    for sale in Sale.objects.all():
        writer.writerow([sale.pk, sale.created_at, sale.cashier_name, sale.total, sale.amount_paid, sale.change_due])
    return response
