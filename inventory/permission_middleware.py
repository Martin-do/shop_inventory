from django.contrib import messages
from django.shortcuts import redirect
from django.urls import Resolver404, resolve

from .access_control import URL_PERMISSION_MAP, permission_name


class GranularPermissionMiddleware:
    """Enforce the permission mapped to each inventory URL before its view runs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                match = resolve(request.path_info)
            except Resolver404:
                match = None
            codename = URL_PERMISSION_MAP.get(getattr(match, "url_name", ""))
            if codename and not (request.user.is_superuser or request.user.has_perm(permission_name(codename))):
                messages.error(request, "Access denied. Your account does not have permission for that action.")
                if getattr(match, "url_name", "") == "dashboard":
                    return redirect("login")
                return redirect("dashboard")
        return self.get_response(request)
