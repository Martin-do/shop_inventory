from django.urls import path

from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("products/", views.product_list, name="product_list"),
    path("products/new/", views.product_create, name="product_create"),
    path("products/<int:pk>/edit/", views.product_update, name="product_update"),
    path("products/<int:pk>/toggle/", views.product_toggle_active, name="product_toggle_active"),
    path("stock/receive/", views.receive_stock, name="receive_stock"),
    path("pos/", views.pos, name="pos"),
    path("pos/add/", views.pos_add, name="pos_add"),
    path("pos/remove/<str:barcode>/", views.pos_remove, name="pos_remove"),
    path("pos/clear/", views.pos_clear, name="pos_clear"),
    path("pos/checkout/", views.pos_checkout, name="pos_checkout"),
    path("sales/<int:sale_id>/", views.sale_detail, name="sale_detail"),
    path("sales/<int:sale_id>/receipt/", views.sale_receipt, name="sale_receipt"),
    path("sales/<int:sale_id>/revert/", views.sale_revert, name="sale_revert"),
    path("reports/", views.reports, name="reports"),
    path("reports/products.csv", views.export_products_csv, name="export_products_csv"),
    path("reports/sales.csv", views.export_sales_csv, name="export_sales_csv"),
    # Settings & custom admin dashboard
    path("settings/", views.settings_dashboard, name="settings_dashboard"),
    path("settings/backup/", views.trigger_manual_backup, name="trigger_manual_backup"),
    path("settings/staff/", views.settings_staff_list, name="settings_staff_list"),
    path("settings/staff/new/", views.settings_staff_create, name="settings_staff_create"),
    path("settings/staff/<int:pk>/edit/", views.settings_staff_update, name="settings_staff_update"),
    path("settings/categories/", views.settings_category_list, name="settings_category_list"),
    path("settings/categories/<int:pk>/edit/", views.settings_category_update, name="settings_category_update"),
    path("settings/categories/<int:pk>/delete/", views.settings_category_delete, name="settings_category_delete"),
    # Customer Profiles
    path("customers/", views.customer_list, name="customer_list"),
    path("customers/new/", views.customer_create, name="customer_create"),
    path("customers/<int:pk>/edit/", views.customer_update, name="customer_update"),
    # API Search
    path("api/products/search/", views.api_product_search, name="api_product_search"),
]

