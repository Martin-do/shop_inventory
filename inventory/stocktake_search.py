from django.db.models import Q
from django.http import JsonResponse

from .models import Product, UserProfile
from .views import role_required


@role_required([UserProfile.ROLE_ADMIN, UserProfile.ROLE_STOCK_CLERK])
def stocktake_product_search(request):
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
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

    return JsonResponse({
        "results": [
            {
                "id": product.pk,
                "name": product.name,
                "barcode": product.barcode,
                "variant": product.variant or "",
                "category": product.category.name if product.category else "",
                "selling_price": str(product.selling_price),
            }
            for product in products
        ]
    })
