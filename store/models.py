from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.templatetags.static import static


class Customer(models.Model):
    user = models.OneToOneField(User, null=True, blank=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=200, null=True, blank=True)
    email = models.CharField(max_length=200, blank=True)

    def __str__(self):
        if self.name:
            return self.name
        if self.user:
            return self.user.username
        return self.email or f'Customer {self.pk}'


class Product(models.Model):
    name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    digital = models.BooleanField(default=False, null=True, blank=True)
    image = models.ImageField(null=True, blank=True)

    def __str__(self):
        return self.name

    @property
    def imageURL(self):
        return self.image.url if self.image else static('images/placeholder.png')


class Order(models.Model):
    class Status(models.TextChoices):
        CART = 'cart', 'Cart'
        PENDING = 'pending', 'Awaiting payment'
        REVIEW = 'review', 'Confirming payment'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Payment failed'

    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True)
    date_ordered = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CART, db_index=True)
    transaction_id = models.CharField(max_length=100, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['customer'],
                condition=Q(status='cart'),
                name='store_one_cart_per_customer',
            ),
        ]

    def __str__(self):
        return str(self.id)

    @property
    def complete(self):
        return self.status == self.Status.PAID

    @property
    def shipping(self):
        return any(
            item.product is not None and not item.product.digital
            for item in self.orderitem_set.select_related('product')
        )

    @property
    def get_cart_total(self):
        return sum((item.get_total for item in self.orderitem_set.select_related('product')), Decimal('0.00'))

    @property
    def get_cart_items(self):
        return sum(item.quantity or 0 for item in self.orderitem_set.all())


class OrderItem(models.Model):
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, null=True)
    quantity = models.IntegerField(default=0, null=True, blank=True)
    # Price agreed at checkout, so later price changes never alter a past order.
    # Empty while the item is still in a cart, which follows the live product price.
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    date_added = models.DateTimeField(auto_now_add=True)

    @property
    def unit_price(self):
        if self.price is not None:
            return self.price
        return self.product.price if self.product else Decimal('0.00')

    @property
    def get_total(self):
        return self.unit_price * (self.quantity or 0)


class ShippingAddress(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True)
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True)
    address = models.CharField(max_length=200)
    city = models.CharField(max_length=200)
    state = models.CharField(max_length=200)
    zipcode = models.CharField(max_length=200)
    date_added = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.address


class Payment(models.Model):
    """One gateway order for one Rento order. Amounts are in the smallest currency unit."""

    class Provider(models.TextChoices):
        RAZORPAY = 'razorpay', 'Razorpay'
        PAYPAL = 'paypal', 'PayPal'

    class Status(models.TextChoices):
        CREATED = 'created', 'Created'
        REVIEW = 'review', 'Awaiting review'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'

    order = models.OneToOneField(Order, on_delete=models.PROTECT)
    provider = models.CharField(max_length=10, choices=Provider.choices, default=Provider.PAYPAL)
    gateway_order_id = models.CharField(max_length=64, unique=True)
    gateway_payment_id = models.CharField(max_length=64, blank=True)
    amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.CREATED, db_index=True)
    error_message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.gateway_order_id} ({self.status})'
