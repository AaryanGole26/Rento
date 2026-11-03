"""Staff roles, kept in sync with the database after every migration.

To give someone a role, a superuser ticks "Staff status" on their account in the
admin and adds them to one of these groups. Staff with no group can log in to the
admin but see nothing. Only superusers can manage accounts and roles, because
neither group has any permission on users or groups.
"""

from django.contrib.auth.models import Group, Permission

READ_ONLY = [
    'view_product', 'view_order', 'view_orderitem',
    'view_shippingaddress', 'view_customer', 'view_payment',
]

ROLES = {
    # Looks up orders and customers to answer questions. Changes nothing.
    'Support Staff': READ_ONLY,
    # Runs the catalogue. Orders and payments stay read-only, so a manager can
    # never mark an order as paid.
    'Store Manager': READ_ONLY + ['add_product', 'change_product', 'delete_product'],
}


def sync_roles(sender, **kwargs):
    for name, codenames in ROLES.items():
        group, _ = Group.objects.get_or_create(name=name)
        group.permissions.set(
            Permission.objects.filter(content_type__app_label='store', codename__in=codenames)
        )
