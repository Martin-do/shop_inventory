from django.contrib import admin

from .models import Category, Product, Sale, SaleItem, StockMovement


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ["name"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "barcode", "selling_price", "stock_on_hand", "reorder_level", "is_active"]
    list_filter = ["is_active", "category"]
    search_fields = ["name", "barcode"]


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ["product", "movement_type", "quantity", "created_at", "note"]
    list_filter = ["movement_type", "created_at"]
    search_fields = ["product__name", "product__barcode", "note"]


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ["id", "created_at", "cashier_name", "total", "amount_paid", "change_due"]
    inlines = [SaleItemInline]
