"""One rule for every approval step: stock cannot be approved or sealed while a
product in it has no selling price.

"Pending" stock is anything counted or received that has not yet been applied to
the system-wide stock: lines of open stocktakes and of unapplied receipts.
"""
from django.db.models import Exists, OuterRef

from .models import Product, StockReceipt, StockReceiptLine
from .stocktake_models import StocktakeCount, StocktakeSession

OPEN_STOCKTAKE_STATUSES = [
    StocktakeSession.STATUS_DRAFT,
    StocktakeSession.STATUS_COUNTING,
    StocktakeSession.STATUS_REVIEW,
]
OPEN_RECEIPT_STATUSES = [StockReceipt.STATUS_DRAFT, StockReceipt.STATUS_SUBMITTED]

PRICE_REQUIRED_MESSAGE = "Set a selling price for {names} before approving. Use Enter prices."


def unpriced(queryset):
    return queryset.filter(selling_price=0)


def pending_unpriced_products():
    """Active unpriced products that sit in unapproved stocktake counts or receipts."""
    in_stocktake = StocktakeCount.objects.filter(product=OuterRef("pk"), session__status__in=OPEN_STOCKTAKE_STATUSES)
    in_receipt = StockReceiptLine.objects.filter(product=OuterRef("pk"), receipt__status__in=OPEN_RECEIPT_STATUSES)
    return unpriced(Product.objects.filter(is_active=True)).filter(Exists(in_stocktake) | Exists(in_receipt))


def unpriced_in_session(session):
    return unpriced(Product.objects.filter(stocktake_counts__session=session)).distinct().order_by("name")


def unpriced_in_receipt(receipt):
    return unpriced(Product.objects.filter(receipt_lines__receipt=receipt)).distinct().order_by("name")


def describe(products, limit=5):
    names = [p.name for p in products[:limit + 1]]
    text = ", ".join(names[:limit])
    return text + ("…" if len(names) > limit else "")


def price_block_message(products):
    return PRICE_REQUIRED_MESSAGE.format(names=describe(products))
