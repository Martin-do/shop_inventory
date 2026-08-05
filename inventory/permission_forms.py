from django import forms
from django.contrib.auth.models import Group, Permission, User

from .access_control import ALL_CODENAMES, PERMISSION_SECTIONS, PRESETS, SENSITIVE_PERMISSIONS
from .models import UserProfile


class StaffAccessForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(), required=False, help_text="Leave blank to keep the current password.")
    job_title = forms.CharField(max_length=120, required=False, help_text="A descriptive title only; permissions determine actual access.")
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
            self.fields["job_title"].initial = getattr(getattr(self.instance, "profile", None), "job_title", "")
            self.fields["permissions"].initial = self.instance.user_permissions.filter(
                content_type__app_label="inventory", codename__in=ALL_CODENAMES
            )
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
        user.is_staff = True
        if commit:
            user.save()
            profile, _ = UserProfile.objects.get_or_create(user=user)
            profile.job_title = self.cleaned_data.get("job_title", "").strip()
            profile.role = UserProfile.ROLE_ADMIN if user.is_superuser else UserProfile.ROLE_CASHIER
            profile.save()

            preset = self.cleaned_data.get("preset") or "custom"
            selected = self.cleaned_data.get("permissions")
            if preset != "custom" and not self.is_bound:
                preset_codes = PRESETS[preset]["permissions"]
                selected = Permission.objects.filter(content_type__app_label="inventory", codename__in=preset_codes)
            user.groups.clear()
            if preset != "custom":
                group = Group.objects.filter(name=PRESETS[preset]["label"]).first()
                if group:
                    user.groups.add(group)
            user.user_permissions.set(selected)
        return user
