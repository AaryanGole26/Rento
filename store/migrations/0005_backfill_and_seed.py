from django.conf import settings
from django.db import migrations

CATALOGUE = [
    ('Headphones', '25.00', 'headphones.jpg'),
    ('Roller Scale', '1.00', 'rollerscale1.jpg'),
    ('Toolkit', '12.00', 'toolkit.jpg'),
    ('Laptop', '100.00', 'laptop1.jpg'),
    ('Glue Gun', '8.00', 'gluegun.jpg'),
    ('Labcoat', '2.00', 'labcoat1.jpg'),
    ('Soldering Iron', '3.00', 'soldering.jpg'),
    ('Mini Drafter', '30.00', 'drafter1.jpg'),
    ('Camera', '175.00', 'camera.jpg'),
]


def backfill(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    Customer = apps.get_model('store', 'Customer')
    OrderItem = apps.get_model('store', 'OrderItem')

    # Every account needs a customer record, with its email filled in.
    for user in User.objects.all():
        customer, created = Customer.objects.get_or_create(user=user, defaults={'email': user.email})
        if not created and not customer.email and user.email:
            customer.email = user.email
            customer.save(update_fields=['email'])

    # Lock in prices on finished orders so catalogue changes can't rewrite history.
    finished = OrderItem.objects.filter(price__isnull=True, product__isnull=False).exclude(order__status='cart')
    for item in finished.select_related('product'):
        item.price = item.product.price
        item.save(update_fields=['price'])


def seed_catalogue(apps, schema_editor):
    # A brand new database (such as a fresh PostgreSQL instance) starts with the catalogue.
    Product = apps.get_model('store', 'Product')
    if not Product.objects.exists():
        Product.objects.bulk_create(
            Product(name=name, price=price, image=image, digital=False)
            for name, price, image in CATALOGUE
        )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('store', '0004_order_status_payment'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
        migrations.RunPython(seed_catalogue, migrations.RunPython.noop),
    ]
