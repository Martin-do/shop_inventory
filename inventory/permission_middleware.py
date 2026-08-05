from django.contrib import messages
from django.shortcuts import redirect

from .access_control import permission_for_request, permission_name


class GranularPermissionMiddleware:
    """Enforce the permission mapped to each inventory URL before its view runs."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            codename = permission_for_request(request)
            if codename and not (request.user.is_superuser or request.user.has_perm(permission_name(codename))):
                messages.error(request, "Access denied. Your account does not have permission for that action.")
                if request.resolver_match and request.resolver_match.url_name == "dashboard":
                    return redirect("login")
                return redirect("dashboard")
        return self.get_response(request)
