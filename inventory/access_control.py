from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import redirect


PERMISSION_SECTIONS = {
    "Point of sale": [
        ("access_pos", "Access POS"),
        ("create_sale", "Create sales"),
        ("apply_discount", "Apply discounts"),
        ("view_own_sales", "View own sales"),
        ("view_all_sales", "View all sales"),
        ("reverse_sale", "Reverse completed sales"),
        ("sync_offline_sales", "Synchronise offline sales"),
    ],
    "Products": [
        ("view_products", "View products"),
        ("create_products", "Create products"),
        ("edit_products", "Edit product details"),
        ("change_selling_price", "Change selling prices"),
        ("view_cost_price", "View cost prices"),
        ("change_cost_price", "Change cost prices"),
        ("toggle_products", "Activate or deactivate products"),
        ("manage_categories", "Manage categories"),
    ],
    "Inventory": [
        ("view_stock", "View stock quantities"),
        ("receive_stock", "Receive stock"),
        ("adjust_stock", "Adjust stock balances"),
        ("view_stock_movements", "View stock movements"),
        ("export_stock", "Export stock records"),
    ],
    "Stocktake": [
        ("view_assigned_stocktakes", "View assigned stocktakes"),
        ("count_assigned_zones", "Count assigned zones"),
        ("create_products_during_stocktake", "Create products during stocktake"),
        ("complete_stocktake_zones", "Complete assigned zones"),
        ("view_all_stocktake_zones", "View all stocktake zones"),
        ("create_stocktakes", "Create stocktake sessions"),
        ("assign_stocktake_teams", "Assign counting teams"),
        ("start_stocktakes", "Start stocktake sessions"),
        ("review_stocktake_counts", "Review counts and request recounts"),
        ("apply_stocktakes", "Apply opening inventory"),
    ],
    "Customers": [
        ("view_customers", "View customers"),
        ("create_customers", "Create customers"),
        ("edit_customers", "Edit customers"),
        ("view_customer_history", "View customer purchase history"),
    ],
    "Reports": [
        ("view_sales_reports", "View sales reports"),
        ("view_stock_valuation", "View stock valuation"),
        ("view_profit_information", "View profit information"),
        ("export_sales", "Export sales"),
        ("export_products", "Export products"),
        ("export_stocktakes", "Export stocktake results"),
    ],
    "Administration": [
        ("view_staff", "View staff accounts"),
        ("create_staff", "Create staff accounts"),
        ("edit_staff", "Edit staff accounts"),
        ("assign_permissions", "Assign staff permissions"),
        ("manage_store_settings", "Manage store settings"),
        ("run_backups", "Run backups"),
        ("view_audit_history", "View audit history"),
        ("export_audit_history", "Export audit history"),
    ],
}

ALL_CODENAMES = [code for rows in PERMISSION_SECTIONS.values() for code, _ in rows]

PRESETS = {
    "cashier": {
        "label": "Cashier",
        "permissions": {"access_pos", "create_sale", "view_own_sales", "view_customers", "create_customers"},
    },
    "senior_cashier": {
        "label": "Senior cashier",
        "permissions": {"access_pos", "create_sale", "apply_discount", "view_own_sales", "view_all_sales", "reverse_sale", "view_customers", "create_customers", "edit_customers"},
    },
    "inventory_counter": {
        "label": "Inventory counter",
        "permissions": {"view_products", "view_stock", "view_assigned_stocktakes", "count_assigned_zones", "create_products_during_stocktake", "complete_stocktake_zones"},
    },
    "storekeeper": {
        "label": "Storekeeper",
        "permissions": {"view_products", "create_products", "edit_products", "view_stock", "receive_stock", "adjust_stock", "view_stock_movements", "manage_categories", "view_assigned_stocktakes", "count_assigned_zones", "create_products_during_stocktake", "complete_stocktake_zones"},
    },
    "inventory_supervisor": {
        "label": "Inventory supervisor",
        "permissions": {"view_products", "create_products", "edit_products", "change_selling_price", "view_cost_price", "change_cost_price", "view_stock", "receive_stock", "adjust_stock", "view_stock_movements", "manage_categories", "view_assigned_stocktakes", "count_assigned_zones", "create_products_during_stocktake", "complete_stocktake_zones", "view_all_stocktake_zones", "create_stocktakes", "assign_stocktake_teams", "start_stocktakes", "review_stocktake_counts"},
    },
    "manager": {"label": "Manager", "permissions": set(ALL_CODENAMES) - {"assign_permissions"}},
    "owner": {"label": "Owner / system administrator", "permissions": set(ALL_CODENAMES)},
    "custom": {"label": "Custom", "permissions": set()},
}

SENSITIVE_PERMISSIONS = {
    "reverse_sale", "adjust_stock", "change_selling_price", "change_cost_price",
    "view_profit_information", "apply_stocktakes", "assign_permissions", "view_audit_history",
}

URL_PERMISSION_MAP = {
    "dashboard": "view_sales_reports",
    "pos": "access_pos", "pos_add": "create_sale", "pos_remove": "create_sale", "pos_clear": "create_sale", "pos_checkout": "create_sale",
    "product_list": "view_products", "product_create": "create_products", "product_update": "edit_products", "product_toggle_active": "toggle_products",
    "receive_stock": "receive_stock",
    "stocktake_list": "view_assigned_stocktakes", "stocktake_detail": "view_assigned_stocktakes", "stocktake_create": "create_stocktakes",
    "stocktake_assign_zone": "assign_stocktake_teams", "stocktake_start": "start_stocktakes", "stocktake_count_zone": "count_assigned_zones",
    "stocktake_save_count": "count_assigned_zones", "stocktake_quick_product": "create_products_during_stocktake", "stocktake_complete_zone": "complete_stocktake_zones",
    "stocktake_submit_review": "review_stocktake_counts", "stocktake_review_count": "review_stocktake_counts", "stocktake_apply": "apply_stocktakes",
    "customer_list": "view_customers", "customer_create": "create_customers", "customer_update": "edit_customers",
    "reports": "view_sales_reports", "export_sales_csv": "export_sales", "export_products_csv": "export_products",
    "audit_history": "view_audit_history", "audit_detail": "view_audit_history", "audit_export_csv": "export_audit_history",
    "settings_dashboard": "manage_store_settings", "trigger_manual_backup": "run_backups",
    "settings_staff_list": "view_staff", "settings_staff_create": "create_staff", "settings_staff_update": "edit_staff",
    "settings_category_list": "manage_categories", "settings_category_update": "manage_categories", "settings_category_delete": "manage_categories",
    "api_product_search": "view_products", "api_active_catalog": "view_products", "api_sync_offline": "sync_offline_sales",
}


def permission_name(codename):
    return f"inventory.{codename}"


def permission_for_request(request):
    match = getattr(request, "resolver_match", None)
    return URL_PERMISSION_MAP.get(getattr(match, "url_name", ""))


def has_access(user, codename):
    return bool(user and user.is_authenticated and (user.is_superuser or user.has_perm(permission_name(codename))))


def permission_required(codename):
    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if has_access(request.user, codename):
                return view_func(request, *args, **kwargs)
            messages.error(request, "Access denied. This staff account does not have the required permission.")
            return redirect("dashboard")
        return wrapped
    return decorator
