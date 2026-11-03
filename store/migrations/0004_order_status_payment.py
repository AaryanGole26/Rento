from django.db import migrations, models
import django.db.models.deletion


def complete_to_status(apps, schema_editor):
    Order = apps.get_model('store', 'Order')
    Order.objects.filter(complete=True).update(status='paid')
    # Unfinished orders with no owner can never be reached as a cart again.
    Order.objects.filter(complete=False, customer__isnull=True).update(status='failed')

    # Each customer may only have one cart: keep the newest, retire the rest.
    seen = set()
    for order in Order.objects.filter(status='cart').order_by('-date_ordered', '-pk'):
        if order.customer_id in seen:
            order.status = 'failed'
            order.save(update_fields=['status'])
        seen.add(order.customer_id)


def status_to_complete(apps, schema_editor):
    apps.get_model('store', 'Order').objects.filter(status='paid').update(complete=True)


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0003_alter_customer_id_alter_order_id_alter_orderitem_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[('cart', 'Cart'), ('pending', 'Awaiting payment'), ('paid', 'Paid'), ('failed', 'Payment failed')],
                db_index=True, default='cart', max_length=10,
            ),
        ),
        migrations.RunPython(complete_to_status, status_to_complete),
        migrations.RemoveField(model_name='order', name='complete'),
        migrations.AddConstraint(
            model_name='order',
            constraint=models.UniqueConstraint(
                condition=models.Q(('status', 'cart')), fields=('customer',), name='store_one_cart_per_customer',
            ),
        ),
        migrations.AlterField(
            model_name='order',
            name='transaction_id',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AlterField(
            model_name='customer',
            name='name',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AlterField(
            model_name='customer',
            name='email',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AlterField(
            model_name='product',
            name='price',
            field=models.DecimalField(decimal_places=2, max_digits=10),
        ),
        migrations.AddField(
            model_name='orderitem',
            name='price',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AlterField(
            model_name='orderitem',
            name='order',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='store.order'),
        ),
        migrations.AlterField(
            model_name='orderitem',
            name='product',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='store.product'),
        ),
        migrations.CreateModel(
            name='Payment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('gateway_order_id', models.CharField(max_length=64, unique=True)),
                ('gateway_payment_id', models.CharField(blank=True, max_length=64)),
                ('amount', models.PositiveIntegerField()),
                ('currency', models.CharField(max_length=3)),
                ('status', models.CharField(
                    choices=[('created', 'Created'), ('paid', 'Paid'), ('failed', 'Failed')],
                    db_index=True, default='created', max_length=10,
                )),
                ('error_message', models.CharField(blank=True, max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to='store.order')),
            ],
        ),
    ]
