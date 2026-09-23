from django.contrib import messages
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render

from .access_control import PRESETS, permission_required
from .permission_forms import StaffAccessForm


def _preset_permissions():
    """Preset -> codenames, so the editor's JavaScript stays in step with the server."""
    return {key: sorted(data["permissions"]) for key, data in PRESETS.items() if key != "custom"}


def _inventory_codes(user):
    return {
        permission.split(".", 1)[1]
        for permission in user.get_all_permissions()
        if permission.startswith("inventory.")
    }


def _may_manage(actor, target):
    """An account may only be edited by someone holding at least its access."""
    if actor.is_superuser:
        return True
    if target.is_superuser or target.pk == actor.pk:
        return False
    return _inventory_codes(target) <= _inventory_codes(actor)


@permission_required("view_staff")
def staff_list(request):
    staff_members = User.objects.prefetch_related("groups", "user_permissions").select_related("profile").order_by("-is_superuser", "username")
    rows = []
    for user in staff_members:
        rows.append({
            "user": user,
            "groups": list(user.groups.values_list("name", flat=True)),
            "permission_count": len(_inventory_codes(user)),
            "can_edit": _may_manage(request.user, user),
        })
    return render(request, "inventory/settings_staff_list.html", {"staff_members": staff_members, "access_rows": rows})


@permission_required("create_staff")
def staff_create(request):
    form = StaffAccessForm(request.POST or None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        messages.success(request, f"Staff account for {user.username} created with {len(user.get_all_permissions())} effective permissions.")
        return redirect("settings_staff_list")
    return render(request, "inventory/settings_staff_access_form.html", {
        "form": form,
        "title": "Create Staff Account",
        "permission_sections": form.permission_sections,
        "preset_permissions": _preset_permissions(),
    })


@permission_required("edit_staff")
def staff_update(request, pk):
    user = get_object_or_404(User, pk=pk)
    if not _may_manage(request.user, user):
        if user.pk == request.user.pk:
            messages.error(request, "You cannot change your own account here. Ask the shop owner.")
        elif user.is_superuser:
            messages.error(request, "Only a system administrator can edit a superuser account.")
        else:
            messages.error(request, "This account has access your own account does not hold, so you cannot edit it.")
        return redirect("settings_staff_list")
    form = StaffAccessForm(request.POST or None, instance=user, actor=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Access settings for {user.username} updated.")
        return redirect("settings_staff_list")
    return render(request, "inventory/settings_staff_access_form.html", {
        "form": form,
        "title": f"Edit Staff Access: {user.username}",
        "permission_sections": form.permission_sections,
        "staff_user": user,
        "preset_permissions": _preset_permissions(),
    })
