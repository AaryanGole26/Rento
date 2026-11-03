from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('store', '0006_revoke_customer_permissions'),
    ]

    operations = [
        migrations.AddField(
            model_name='payment',
            name='provider',
            field=models.CharField(
                choices=[('razorpay', 'Razorpay'), ('paypal', 'PayPal')], default='paypal', max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='payment',
            name='status',
            field=models.CharField(
                choices=[('created', 'Created'), ('review', 'Awaiting review'), ('paid', 'Paid'), ('failed', 'Failed')],
                db_index=True, default='created', max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='order',
            name='status',
            field=models.CharField(
                choices=[('cart', 'Cart'), ('pending', 'Awaiting payment'), ('review', 'Confirming payment'),
                         ('paid', 'Paid'), ('failed', 'Payment failed')],
                db_index=True, default='cart', max_length=10,
            ),
        ),
    ]
