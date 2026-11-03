from django.conf import settings
from django.db import migrations


def revoke(apps, schema_editor):
    # Ordinary customers should hold no admin permissions. They grant nothing while
    # the account is non-staff, but would apply at once if it were ever made staff.
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    for user in User.objects.filter(is_staff=False, is_superuser=False):
        user.user_permissions.clear()
        user.groups.clear()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('store', '0005_backfill_and_seed'),
    ]

    operations = [
        migrations.RunPython(revoke, migrations.RunPython.noop),
    ]
