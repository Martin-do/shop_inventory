import csv
import json

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render

from .models import AuditLog, UserProfile
from .views import role_required


@role_required([UserProfile.ROLE_ADMIN])
def audit_history(request):
    logs = AuditLog.objects.select_related("actor")
    query = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    actor = request.GET.get("actor", "").strip()
    object_type = request.GET.get("object_type", "").strip()

    if query:
        logs = logs.filter(
            Q(summary__icontains=query)
            | Q(object_label__icontains=query)
            | Q(object_id__icontains=query)
            | Q(actor_name__icontains=query)
            | Q(request_path__icontains=query)
        )
    if action:
        logs = logs.filter(action=action)
    if actor:
        logs = logs.filter(actor_name__icontains=actor)
    if object_type:
        logs = logs.filter(object_type=object_type)

    paginator = Paginator(logs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    context = {
        "page_obj": page_obj,
        "query": query,
        "selected_action": action,
        "selected_actor": actor,
        "selected_object_type": object_type,
        "actions": AuditLog.ACTION_CHOICES,
        "object_types": AuditLog.objects.exclude(object_type="").values_list("object_type", flat=True).distinct().order_by("object_type"),
    }
    return render(request, "inventory/audit_history.html", context)


@role_required([UserProfile.ROLE_ADMIN])
def audit_detail(request, pk):
    log = get_object_or_404(AuditLog.objects.select_related("actor"), pk=pk)
    return render(request, "inventory/audit_detail.html", {"log": log})


@role_required([UserProfile.ROLE_ADMIN])
def audit_export_csv(request):
    logs = AuditLog.objects.select_related("actor").all()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="staff_action_history.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Timestamp", "Staff", "Action", "Object Type", "Object ID", "Object",
        "Summary", "Before", "After", "Metadata", "Method", "Path", "IP Address"
    ])
    for log in logs:
        writer.writerow([
            log.created_at, log.actor_name, log.action, log.object_type, log.object_id,
            log.object_label, log.summary, json.dumps(log.before, ensure_ascii=False),
            json.dumps(log.after, ensure_ascii=False), json.dumps(log.metadata, ensure_ascii=False),
            log.request_method, log.request_path, log.ip_address or "",
        ])
    return response
