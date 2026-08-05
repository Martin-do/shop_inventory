from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from .access_control import URL_PERMISSION_MAP, has_access


class GranularPermissionMiddleware:
    """Enforce mapped permissions while preserving legacy-role compatibility."""

    def __init__(self, get_response):
        self.get_response = get_response

    def _landing_page(self, user):
        for codename, route in (
            ("access_pos", "pos"),
            ("receive_stock", "receive_stock"),
            ("view_assigned_stocktakes", "stocktake_list"),
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
            if codename and not has_access(request.user, codename):
                if request.path_info.startswith("/api/") or request.path_info.startswith("/stocktakes/api/"):
                    return JsonResponse(
                        {"error": "Your account does not have permission for this action."},
                        status=403,
                    )
                messages.error(request, "Access denied. Your account does not have permission for that action.")
                if url_name == "dashboard":
                    return redirect(self._landing_page(request.user))
                return redirect(self._landing_page(request.user))
        return self.get_response(request)
