from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .audit_context import get_current_request
from .models import AuditLog, BackupLog, Category, Customer, Product, Sale, SaleItem, StockMovement, StoreSettings, UserProfile

AUDITED_MODELS = (Category, Customer, Product, Sale, SaleItem, StockMovement, StoreSettings, UserProfile)
_before_state = {}


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "pk"):
        return value.pk
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _snapshot(instance):
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in {"password", "google_service_account_json"}:
            data[field.name] = "[REDACTED]"
            continue
        data[field.name] = _json_value(getattr(instance, field.name, None))
    return data


def _request_details():
    request = get_current_request()
    if not request:
        return {}, None
    actor = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
    ip = forwarded or request.META.get("REMOTE_ADDR")
    return {
        "request_method": request.method,
        "request_path": request.path[:255],
        "ip_address": ip or None,
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
    }, actor


def log_event(action, summary, instance=None, before=None, after=None, metadata=None, actor=None):
    request_fields, request_actor = _request_details()
    actor = actor or request_actor
    AuditLog.objects.create(
        actor=actor,
        actor_name=(actor.get_full_name() or actor.username) if actor else "System",
        action=action,
        object_type=instance._meta.label_lower if instance is not None else "",
        object_id=str(instance.pk or "") if instance is not None else "",
        object_label=str(instance)[:240] if instance is not None else "",
        summary=summary[:240],
        before=before or {},
        after=after or {},
        metadata=metadata or {},
        **request_fields,
    )


@receiver(pre_save)
def capture_before_state(sender, instance, **kwargs):
    if sender not in AUDITED_MODELS or not instance.pk:
        return
    current = sender.objects.filter(pk=instance.pk).first()
    if current:
        _before_state[(sender, instance.pk)] = _snapshot(current)


@receiver(post_save)
def record_save(sender, instance, created, **kwargs):
    if sender not in AUDITED_MODELS:
        return
    after = _snapshot(instance)
    before = _before_state.pop((sender, instance.pk), {})
    action = AuditLog.ACTION_CREATE if created else AuditLog.ACTION_UPDATE
    changed = {
        key: {"from": before.get(key), "to": value}
        for key, value in after.items()
        if before.get(key) != value
    }
    if not created and not changed:
        return
    label = sender._meta.verbose_name.title()
    log_event(action, f"{label} {'created' if created else 'updated'}", instance, before, after, {"changes": changed})


@receiver(post_delete)
def record_delete(sender, instance, **kwargs):
    if sender not in AUDITED_MODELS:
        return
    log_event(AuditLog.ACTION_DELETE, f"{sender._meta.verbose_name.title()} deleted", instance, _snapshot(instance), {})


@receiver(user_logged_in)
def record_login(sender, request, user, **kwargs):
    log_event(AuditLog.ACTION_LOGIN, "Staff logged in", actor=user, metadata={"username": user.username})


@receiver(user_logged_out)
def record_logout(sender, request, user, **kwargs):
    if user:
        log_event(AuditLog.ACTION_LOGOUT, "Staff logged out", actor=user, metadata={"username": user.username})
