from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from .access_control import URL_PERMISSION_MAP, has_access


STOCKTAKE_ENTRY_PERMISSIONS = (
    "view_assigned_stocktakes",
    "count_assigned_zones",
    "create_products_during_stocktake",
    "complete_stocktake_zones",
    "view_all_stocktake_zones",
    "assign_stocktake_teams",
    "start_stocktakes",
    "review_stocktake_counts",
    "apply_stocktakes",
)


POS_LOOKUP_ENDPOINTS = {"api_product_search", "api_active_catalog"}
POS_LOOKUP_PERMISSIONS = ("access_pos", "view_products")


class GranularPermissionMiddleware:
    """Enforce mapped permissions while preserving legacy-role compatibility."""

    def __init__(self, get_response):
        self.get_response = get_response

    def _can_enter_stocktake(self, user):
        return any(has_access(user, codename) for codename in STOCKTAKE_ENTRY_PERMISSIONS)

    def _has_required_access(self, user, url_name, codename):
        if url_name in {"stocktake_list", "stocktake_detail"}:
            return self._can_enter_stocktake(user)
        if url_name in POS_LOOKUP_ENDPOINTS:
            # The checkout screen cannot work without looking products up, so
            # anyone allowed to use the POS may read the catalogue it sells from.
            return any(has_access(user, code) for code in POS_LOOKUP_PERMISSIONS)
        if url_name in {"sale_detail", "sale_receipt"}:
            # The view itself limits which sales are visible.
            return has_access(user, "view_own_sales") or has_access(user, "view_all_sales")
        return has_access(user, codename)

    def _landing_page(self, user):
        for codename, route in (
            ("access_pos", "pos"),
            ("receive_stock", "receive_stock"),
        ):
            if has_access(user, codename):
                return route
        if self._can_enter_stocktake(user):
            return "stocktake_list"
        for codename, route in (
            ("view_products", "product_list"),
            ("view_customers", "customer_list"),
        ):
            if has_access(user, codename):
                return route
        return "login"

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                match = resolve(request.path_info)
            except Resolver404:
                match = None
            url_name = getattr(match, "url_name", "")
            codename = URL_PERMISSION_MAP.get(url_name)
            if url_name == "stocktake_product_search":
                codename = "count_assigned_zones"
            if codename and not self._has_required_access(request.user, url_name, codename):
                if request.path_info.startswith("/api/") or request.path_info.startswith("/stocktakes/api/"):
                    return JsonResponse(
                        {"error": "Your account does not have permission for this action."},
                        status=403,
                    )
                if url_name != "dashboard":
                    # Everyone lands on the dashboard after login. Staff without
                    # reporting access are simply sent to their own home page,
                    # without an "access denied" error on every sign-in.
                    messages.error(request, "Access denied. Your account does not have permission for that action.")
                return redirect(self._landing_page(request.user))
        return self.get_response(request)
