from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_create, name="product_create"),
    path("stock/receive/", views.receive_stock, name="receive_stock"),
    path("pos/", views.pos, name="pos"),
    path("pos/add/", views.pos_add, name="pos_add"),
    path("pos/remove/<str:barcode>/", views.pos_remove, name="pos_remove"),
    path("pos/clear/", views.pos_clear, name="pos_clear"),
    path("pos/checkout/", views.pos_checkout, name="pos_checkout"),
    path("sales/<int:sale_id>/", views.sale_detail, name="sale_detail"),
    path("reports/", views.reports, name="reports"),
    path("reports/products.csv", views.export_products_csv, name="export_products_csv"),
    path("reports/sales.csv", views.export_sales_csv, name="export_sales_csv"),
]
