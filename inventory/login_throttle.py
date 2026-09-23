"""Slow down password guessing against the login page.

Failures are counted per username and per client address. Once either counter
passes the configured limit, further attempts are refused until the cool-down
expires. A successful login clears both counters.
"""

import logging

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import redirect

logger = logging.getLogger("inventory")

LOGIN_PATHS = {"/accounts/login/", "/login/"}


def client_ip(request):
    """The caller's address, trusting proxy headers only when configured to."""
    if getattr(settings, "TRUST_FORWARDED_FOR", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.META.get("REMOTE_ADDR") or "unknown"


def _keys(request):
    username = (request.POST.get("username") or "").strip().lower()[:150]
    keys = [f"login-throttle:ip:{client_ip(request)}"]
    if username:
        keys.append(f"login-throttle:user:{username}")
    return keys


def _limit():
    return getattr(settings, "LOGIN_ATTEMPT_LIMIT", 8)


def _cooldown():
    return getattr(settings, "LOGIN_ATTEMPT_COOLDOWN_SECONDS", 15 * 60)


def is_locked_out(request):
    return any((cache.get(key) or 0) >= _limit() for key in _keys(request))


def record_failure(request):
    for key in _keys(request):
        added = cache.add(key, 1, _cooldown())
        if not added:
            try:
                cache.incr(key)
            except ValueError:
                cache.set(key, 1, _cooldown())


def clear_failures(request):
    cache.delete_many(_keys(request))


class LoginThrottleMiddleware:
    """Refuse repeated failed logins from the same account or address."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        is_login_post = request.method == "POST" and request.path_info in LOGIN_PATHS
        if not is_login_post:
            return self.get_response(request)

        if is_locked_out(request):
            logger.warning(
                "Login blocked by throttle for %s from %s",
                (request.POST.get("username") or "")[:150],
                client_ip(request),
            )
            messages.error(
                request,
                "Too many failed login attempts. Wait a few minutes and try again, "
                "or ask the shop owner to reset your password.",
            )
            return redirect(settings.LOGIN_URL)

        response = self.get_response(request)

        # The auth view answers a good login with a redirect and a logged-in user.
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            clear_failures(request)
        else:
            record_failure(request)
            logger.info(
                "Failed login for %s from %s",
                (request.POST.get("username") or "")[:150],
                client_ip(request),
            )
        return response
