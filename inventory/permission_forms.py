from django import forms
from django.contrib.auth.models import Group, Permission, User

from .access_control import ALL_CODENAMES, PERMISSION_SECTIONS, PRESETS, SENSITIVE_PERMISSIONS
from .models import UserProfile


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
                        "sensitive": codename in SENSITIVE_PERMISSIONS,
                    })
            self.permission_sections.append((section, entries))

        if self.instance.pk:
            effective_codes = {
                value.split(".", 1)[1]
                for value in self.instance.get_all_permissions()
                if value.startswith("inventory.")
            }
            self.fields["permissions"].initial = permission_qs.filter(codename__in=effective_codes)
            matching_group = self.instance.groups.filter(name__in=[data["label"] for data in PRESETS.values()]).first()
            if matching_group:
                for key, data in PRESETS.items():
                    if data["label"] == matching_group.name:
                        self.fields["preset"].initial = key
                        break
        else:
            self.fields["password"].required = True
            self.fields["password"].help_text = "Required for a new staff account."

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
        # Legacy role decorators remain temporarily; the central permission gate is authoritative.
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
                # The zero-permission case needs an explicit marker so it cannot be
                # mistaken for an unmigrated legacy administrator account.
                group, _ = Group.objects.get_or_create(name=PRESETS["custom"]["label"])
                group.permissions.clear()
                user.groups.add(group)
            elif selected_codes == set(PRESETS[preset]["permissions"]):
                group = Group.objects.filter(name=PRESETS[preset]["label"]).first()
                if group:
                    user.groups.add(group)

            # Exact direct permissions are authoritative. Preset group membership is
            # descriptive and is only retained when the selection exactly matches it.
            user.user_permissions.set(selected)
        return user
