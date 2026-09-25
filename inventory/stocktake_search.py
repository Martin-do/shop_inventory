from django.db.models import Q
from django.http import JsonResponse

from .access_control import has_access
from .models import Product, UserProfile
from .views import role_required


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
def stocktake_product_search(request):
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

    can_view_cost = has_access(request.user, "view_cost_price") or has_access(request.user, "change_cost_price")
    results = []
    for product in products:
        item = {
            "id": product.pk,
            "name": product.name,
            "barcode": product.barcode,
            "barcode_display": "No barcode" if product.barcode.startswith("MANUAL-") else product.barcode,
            "variant": product.variant or "",
            "category": product.category.name if product.category else "",
            "selling_price": str(product.selling_price),
            "reorder_level": product.reorder_level,
        }
        if can_view_cost:
            item["cost_price"] = str(product.cost_price)
        results.append(item)

    return JsonResponse({"results": results})
