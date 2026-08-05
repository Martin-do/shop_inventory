from django.db import migrations


PERMISSIONS = {
    "Point of sale": [("access_pos", "Can access POS"), ("create_sale", "Can create sales"), ("apply_discount", "Can apply discounts"), ("view_own_sales", "Can view own sales"), ("view_all_sales", "Can view all sales"), ("reverse_sale", "Can reverse completed sales"), ("sync_offline_sales", "Can synchronise offline sales")],
    "Products": [("view_products", "Can view products"), ("create_products", "Can create products"), ("edit_products", "Can edit product details"), ("change_selling_price", "Can change selling prices"), ("view_cost_price", "Can view cost prices"), ("change_cost_price", "Can change cost prices"), ("toggle_products", "Can activate or deactivate products"), ("manage_categories", "Can manage categories")],
    "Inventory": [("view_stock", "Can view stock quantities"), ("receive_stock", "Can receive stock"), ("adjust_stock", "Can adjust stock balances"), ("view_stock_movements", "Can view stock movements"), ("export_stock", "Can export stock records")],
    "Stocktake": [("view_assigned_stocktakes", "Can view assigned stocktakes"), ("count_assigned_zones", "Can count assigned stocktake zones"), ("create_products_during_stocktake", "Can create products during stocktake"), ("complete_stocktake_zones", "Can complete assigned zones"), ("view_all_stocktake_zones", "Can view all stocktake zones"), ("create_stocktakes", "Can create stocktake sessions"), ("assign_stocktake_teams", "Can assign stocktake teams"), ("start_stocktakes", "Can start stocktake sessions"), ("review_stocktake_counts", "Can review stocktake counts"), ("apply_stocktakes", "Can apply opening inventory")],
    "Customers": [("view_customers", "Can view customers"), ("create_customers", "Can create customers"), ("edit_customers", "Can edit customers"), ("view_customer_history", "Can view customer purchase history")],
    "Reports": [("view_sales_reports", "Can view sales reports"), ("view_stock_valuation", "Can view stock valuation"), ("view_profit_information", "Can view profit information"), ("export_sales", "Can export sales"), ("export_products", "Can export products"), ("export_stocktakes", "Can export stocktake results")],
    "Administration": [("view_staff", "Can view staff accounts"), ("create_staff", "Can create staff accounts"), ("edit_staff", "Can edit staff accounts"), ("assign_permissions", "Can assign staff permissions"), ("manage_store_settings", "Can manage store settings"), ("run_backups", "Can run backups"), ("view_audit_history", "Can view audit history"), ("export_audit_history", "Can export audit history")],
}

PRESETS = {
    "Cashier": {"access_pos", "create_sale", "view_own_sales", "view_customers", "create_customers"},
    "Senior cashier": {"access_pos", "create_sale", "apply_discount", "view_own_sales", "view_all_sales", "reverse_sale", "view_customers", "create_customers", "edit_customers"},
    "Inventory counter": {"view_products", "view_stock", "view_assigned_stocktakes", "count_assigned_zones", "create_products_during_stocktake", "complete_stocktake_zones"},
    "Storekeeper": {"view_products", "create_products", "edit_products", "view_stock", "receive_stock", "adjust_stock", "view_stock_movements", "manage_categories", "view_assigned_stocktakes", "count_assigned_zones", "create_products_during_stocktake", "complete_stocktake_zones"},
    "Inventory supervisor": {"view_products", "create_products", "edit_products", "change_selling_price", "view_cost_price", "change_cost_price", "view_stock", "receive_stock", "adjust_stock", "view_stock_movements", "manage_categories", "view_assigned_stocktakes", "count_assigned_zones", "create_products_during_stocktake", "complete_stocktake_zones", "view_all_stocktake_zones", "create_stocktakes", "assign_stocktake_teams", "start_stocktakes", "review_stocktake_counts"},
}


def create_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    ContentType = apps.get_model("contenttypes", "ContentType")
    UserProfile = apps.get_model("inventory", "UserProfile")
    content_type, _ = ContentType.objects.get_or_create(app_label="inventory", model="userprofile")
    permission_map = {}
    for rows in PERMISSIONS.values():
        for codename, name in rows:
            permission, _ = Permission.objects.get_or_create(content_type=content_type, codename=codename, defaults={"name": name})
            if permission.name != name:
                permission.name = name
                permission.save(update_fields=["name"])
            permission_map[codename] = permission

    all_codes = set(permission_map)
    presets = dict(PRESETS)
    presets["Manager"] = all_codes - {"assign_permissions"}
    presets["Owner / system administrator"] = all_codes
    for name, codes in presets.items():
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set([permission_map[code] for code in codes])

    legacy = {"cashier": "Cashier", "stock_clerk": "Storekeeper", "admin": "Manager"}
    for profile in UserProfile.objects.select_related("user"):
        if profile.user.is_superuser:
            group = Group.objects.get(name="Owner / system administrator")
        else:
            group = Group.objects.get(name=legacy.get(profile.role, "Cashier"))
        profile.user.groups.add(group)


class Migration(migrations.Migration):
    dependencies = [("inventory", "0011_rename_inventory_a_object__b3e659_idx_inventory_a_object__6617b5_idx")]

    operations = [migrations.RunPython(create_permissions, migrations.RunPython.noop)]
