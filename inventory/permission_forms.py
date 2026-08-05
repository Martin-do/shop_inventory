from django import forms
from django.contrib.auth.models import Group, Permission, User

from .access_control import ALL_CODENAMES, PERMISSION_SECTIONS, PRESETS, SENSITIVE_PERMISSIONS
from .models import UserProfile


PERMISSION_DESCRIPTIONS = {
    "access_pos": "Allows the staff member to open and use the point-of-sale screen.",
    "create_sale": "Allows adding items to the cart and completing a customer sale.",
    "apply_discount": "Allows reducing a sale total by entering a discount during checkout.",
    "view_own_sales": "Allows viewing receipts and transactions completed by this staff account.",
    "view_all_sales": "Allows viewing sales completed by every cashier, not only their own.",
    "reverse_sale": "Allows cancelling a completed sale and restoring its sold quantities to stock. A reason is required.",
    "sync_offline_sales": "Allows transactions recorded offline to be submitted to the server when connectivity returns.",
    "view_products": "Allows opening the product catalogue and searching product records.",
    "create_products": "Allows adding new products to the catalogue outside a stocktake.",
    "edit_products": "Allows changing product names, variants, categories and other catalogue details.",
    "change_selling_price": "Allows changing the price customers are charged for a product.",
    "view_cost_price": "Allows seeing product purchase costs and cost-based values.",
    "change_cost_price": "Allows changing the recorded purchase cost of a product.",
    "toggle_products": "Allows activating or deactivating products so they appear or disappear from normal sales workflows.",
    "manage_categories": "Allows creating, renaming and deleting product categories.",
    "view_stock": "Allows seeing current available quantities for products.",
    "receive_stock": "Allows recording newly delivered stock and increasing product quantities.",
    "adjust_stock": "Allows manually increasing or decreasing an existing stock balance with an audit reason.",
    "view_stock_movements": "Allows viewing receipts, sales, returns and adjustments that changed stock.",
    "export_stock": "Allows downloading stock records for use outside the application.",
    "view_assigned_stocktakes": "Allows seeing stocktake sessions and zones assigned to this staff member.",
    "count_assigned_zones": "Allows entering physical product counts in zones assigned to this staff member.",
    "create_products_during_stocktake": "Allows adding a missing product while counting opening inventory.",
    "complete_stocktake_zones": "Allows marking an assigned counting zone as finished.",
    "view_all_stocktake_zones": "Allows seeing every zone and team in a stocktake, including zones not assigned to the staff member.",
    "create_stocktakes": "Allows creating a new opening-inventory or reconciliation session and defining its zones.",
    "assign_stocktake_teams": "Allows assigning staff members to stocktake zones.",
    "start_stocktakes": "Allows moving a draft stocktake into active counting.",
    "review_stocktake_counts": "Allows approving submitted counts, correcting approved quantities and requesting recounts.",
    "apply_stocktakes": "Allows final approved stocktake totals to become the shop's actual sellable stock balances.",
    "view_customers": "Allows opening and searching the customer list.",
    "create_customers": "Allows registering a new customer during or outside a sale.",
    "edit_customers": "Allows changing customer contact details and notes.",
    "view_customer_history": "Allows viewing a customer's previous purchases and receipts.",
    "view_sales_reports": "Allows viewing sales summaries and operational report pages.",
    "view_stock_valuation": "Allows seeing the monetary value of stock currently held by the shop.",
    "view_profit_information": "Allows seeing profit-related figures derived from selling and cost prices.",
    "export_sales": "Allows downloading sales records as a CSV file.",
    "export_products": "Allows downloading the product catalogue as a CSV file.",
    "export_stocktakes": "Allows downloading stocktake counts and approved results.",
    "view_staff": "Allows viewing the list of staff accounts and their access summaries.",
    "create_staff": "Allows creating a new staff login account.",
    "edit_staff": "Allows changing staff identity details, password and account status.",
    "assign_permissions": "Allows selecting the exact permissions granted to other staff accounts. A non-owner cannot grant access they do not possess.",
    "manage_store_settings": "Allows changing store identity, receipt, tax and integration settings.",
    "run_backups": "Allows starting a manual data backup and viewing backup results.",
    "view_audit_history": "Allows viewing who performed audited actions, when they occurred and what changed.",
    "export_audit_history": "Allows downloading the complete staff action history as a CSV file.",
}


class StaffAccessForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(), required=False, help_text="Leave blank to keep the current password.")
    preset = forms.ChoiceField(
        choices=[(key, data["label"]) for key, data in PRESETS.items()],
        required=False,
        initial="custom",
        help_text="Choose a starting permission bundle, then customise individual access below.",
    )
    permissions = forms.ModelMultipleChoiceField(
        queryset=Permission.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = User
        fields = ["username", "first_name", "last_name", "email", "is_active"]

    def __init__(self, *args, actor=None, **kwargs):
        self.actor = actor
        super().__init__(*args, **kwargs)
        permission_qs = Permission.objects.filter(
            content_type__app_label="inventory",
            codename__in=ALL_CODENAMES,
        ).select_related("content_type").order_by("name")
        self.fields["permissions"].queryset = permission_qs

        selected_codes = set()
        if self.is_bound:
            selected_ids = {str(value) for value in self.data.getlist("permissions")}
            selected_codes = set(permission_qs.filter(pk__in=selected_ids).values_list("codename", flat=True))
        elif self.instance.pk:
            direct_codes = set(
                self.instance.user_permissions.filter(
                    content_type__app_label="inventory", codename__in=ALL_CODENAMES
                ).values_list("codename", flat=True)
            )
            has_custom_marker = self.instance.groups.filter(name=PRESETS["custom"]["label"]).exists()
            if direct_codes or has_custom_marker:
                selected_codes = direct_codes
            else:
                selected_codes = {
                    value.split(".", 1)[1]
                    for value in self.instance.get_all_permissions()
                    if value.startswith("inventory.")
                }
            self.fields["permissions"].initial = permission_qs.filter(codename__in=selected_codes)

            matching_group = self.instance.groups.filter(name__in=[data["label"] for data in PRESETS.values()]).first()
            if matching_group:
                for key, data in PRESETS.items():
                    if data["label"] == matching_group.name:
                        self.fields["preset"].initial = key
                        break
            elif selected_codes:
                self.fields["preset"].initial = "custom"
        else:
            self.fields["password"].required = True
            self.fields["password"].help_text = "Required for a new staff account."

        self.permission_sections = []
        by_code = {p.codename: p for p in permission_qs}
        for section, rows in PERMISSION_SECTIONS.items():
            entries = []
            for codename, label in rows:
                permission = by_code.get(codename)
                if permission:
                    entries.append({
                        "permission": permission,
                        "codename": codename,
                        "label": label,
                        "description": PERMISSION_DESCRIPTIONS.get(codename, permission.name),
                        "sensitive": codename in SENSITIVE_PERMISSIONS,
                        "checked": codename in selected_codes,
                    })
            self.permission_sections.append((section, entries))

    def clean_permissions(self):
        selected = self.cleaned_data.get("permissions")
        if self.actor and not self.actor.is_superuser:
            actor_codes = set(self.actor.get_all_permissions())
            forbidden = [p for p in selected if f"inventory.{p.codename}" not in actor_codes]
            if forbidden:
                raise forms.ValidationError("You cannot grant permissions that your own account does not possess.")
        return selected

    def save(self, commit=True):
        user = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            user.set_password(password)
        user.is_staff = True
        if commit:
            user.save()
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.role = UserProfile.ROLE_ADMIN
            profile.save(update_fields=["role"])

            preset = self.cleaned_data.get("preset") or "custom"
            selected = self.cleaned_data.get("permissions")
            selected_codes = {permission.codename for permission in selected}
            user.groups.clear()

            if preset == "custom":
                group, _ = Group.objects.get_or_create(name=PRESETS["custom"]["label"])
                group.permissions.clear()
                user.groups.add(group)
            elif selected_codes == set(PRESETS[preset]["permissions"]):
                group = Group.objects.filter(name=PRESETS[preset]["label"]).first()
                if group:
                    user.groups.add(group)
            else:
                group, _ = Group.objects.get_or_create(name=PRESETS["custom"]["label"])
                group.permissions.clear()
                user.groups.add(group)

            user.user_permissions.set(selected)
        return user
