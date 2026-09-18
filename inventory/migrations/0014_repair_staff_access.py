"""Repair access for accounts created before permissions were enforced properly.

Three corrections:
1. Staff accounts were all marked is_staff, which also opens the Django admin
   site. Only superusers keep it.
2. Checkout submits through the offline-sync endpoint, so any account that can
   create a sale also needs sync_offline_sales, or its sales never reach the
   server. This adds it to those accounts and to the cashier preset groups.
3. Accounts that could previously reverse sales through their admin profile
   role keep that ability explicitly, but only if they hold the manager or
   owner preset. Everyone else loses it.
"""

from django.db import migrations

CASHIER_PRESET_GROUPS = ["Cashier", "Senior cashier"]
FULL_ACCESS_GROUPS = ["Manager", "Owner / system administrator"]


def repair(apps, schema_editor):
    User = apps.get_model("auth", "User")
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")

    User.objects.filter(is_staff=True, is_superuser=False).update(is_staff=False)

    try:
        sync = Permission.objects.get(content_type__app_label="inventory", codename="sync_offline_sales")
        create_sale = Permission.objects.get(content_type__app_label="inventory", codename="create_sale")
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(name__in=CASHIER_PRESET_GROUPS):
        group.permissions.add(sync)

    # Direct permissions: any account that can ring up a sale can submit it.
    for user in User.objects.filter(user_permissions=create_sale).exclude(user_permissions=sync):
        user.user_permissions.add(sync)

    # Group-based: members of a cashier preset are covered by the group update above.
    reverse_sale = Permission.objects.filter(
        content_type__app_label="inventory", codename="reverse_sale"
    ).first()
    if reverse_sale is None:
        return
    full_access_ids = set(
        User.objects.filter(groups__name__in=FULL_ACCESS_GROUPS).values_list("pk", flat=True)
    )
    for user in User.objects.filter(pk__in=full_access_ids):
        user.user_permissions.add(reverse_sale)


def undo(apps, schema_editor):
    # Access changes are not restored: doing so would re-open the Django admin
    # site to ordinary staff accounts.
    pass


class Migration(migrations.Migration):
    dependencies = [("inventory", "0013_merge_0011_0012")]

    operations = [migrations.RunPython(repair, undo)]
