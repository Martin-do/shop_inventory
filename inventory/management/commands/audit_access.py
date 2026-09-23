"""Report how every staff account is wired to permissions, and flag risks.

Read-only. Run on the server after deployments and whenever staff change:

    python manage.py audit_access
    python manage.py audit_access --user amaka        # full permission list for one account
"""

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

from inventory.access_control import (
    ALL_CODENAMES,
    PERMISSION_SECTIONS,
    SENSITIVE_PERMISSIONS,
    has_access,
    is_granularly_configured,
)


def _source(user):
    if user.is_superuser:
        return "owner (superuser)"
    if is_granularly_configured(user):
        groups = ", ".join(user.groups.values_list("name", flat=True)) or "direct permissions"
        return f"granular: {groups}"
    return f"LEGACY role fallback: {getattr(user, 'role', '?')}"


class Command(BaseCommand):
    help = "Show each account's effective access and flag anything risky. Changes nothing."

    def add_arguments(self, parser):
        parser.add_argument("--user", help="Show the full permission list for one username.")

    def handle(self, *args, **options):
        if options["user"]:
            return self._detail(options["user"])

        users = list(User.objects.order_by("-is_superuser", "username"))
        warnings = []
        self.stdout.write(f"{'ACCOUNT':<20}{'ACTIVE':<8}{'PERMS':<7}{'ACCESS SOURCE'}")
        self.stdout.write("-" * 78)
        for user in users:
            granted = [code for code in ALL_CODENAMES if has_access(user, code)]
            self.stdout.write(
                f"{user.username:<20}{('yes' if user.is_active else 'NO'):<8}{len(granted):<7}{_source(user)}"
            )

            if user.is_superuser:
                continue
            if user.is_staff:
                warnings.append(f"{user.username}: is_staff is set, which opens the Django admin site.")
            if user.is_active and not is_granularly_configured(user):
                warnings.append(
                    f"{user.username}: never configured in the permission editor; access comes from the "
                    f"legacy '{getattr(user, 'role', '?')}' role. Open the account and save it once."
                )
            if user.is_active and not granted:
                warnings.append(f"{user.username}: active but has no permissions, so cannot do anything.")
            risky = sorted(code for code in granted if code in SENSITIVE_PERMISSIONS)
            if risky and len(risky) >= 5:
                warnings.append(f"{user.username}: holds {len(risky)} sensitive permissions ({', '.join(risky)}).")

        superusers = [u for u in users if u.is_superuser and u.is_active]
        if len(superusers) == 0:
            warnings.append("No active owner (superuser) account exists.")
        if len(superusers) > 2:
            warnings.append(f"{len(superusers)} owner accounts exist; owners bypass every permission check.")

        self.stdout.write("")
        if warnings:
            self.stdout.write(self.style.WARNING(f"{len(warnings)} thing(s) to review:"))
            for line in warnings:
                self.stdout.write(f"  - {line}")
        else:
            self.stdout.write(self.style.SUCCESS("No access problems found."))

    def _detail(self, username):
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stderr.write(f"No account named '{username}'.")
            return
        self.stdout.write(f"{user.username} - {_source(user)}")
        for section, rows in PERMISSION_SECTIONS.items():
            self.stdout.write(f"\n{section}")
            for code, label in rows:
                mark = "[x]" if has_access(user, code) else "[ ]"
                flag = "  (sensitive)" if code in SENSITIVE_PERMISSIONS else ""
                self.stdout.write(f"  {mark} {label}{flag}")
