from django.contrib import messages
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render

from .access_control import permission_required
from .permission_forms import StaffAccessForm


@permission_required("view_staff")
def staff_list(request):
    staff_members = User.objects.prefetch_related("groups", "user_permissions").select_related("profile").order_by("-is_superuser", "username")
    rows = []
    for user in staff_members:
        effective = sorted(
            permission.split(".", 1)[1]
            for permission in user.get_all_permissions()
            if permission.startswith("inventory.")
        )
        rows.append({
            "user": user,
            "groups": list(user.groups.values_list("name", flat=True)),
            "permission_count": len(effective),
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
    })


@permission_required("edit_staff")
def staff_update(request, pk):
    user = get_object_or_404(User, pk=pk)
    if user.is_superuser and not request.user.is_superuser:
        messages.error(request, "Only a system administrator can edit a superuser account.")
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
    })
