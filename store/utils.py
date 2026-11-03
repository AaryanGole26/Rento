import json

from django.db.models import Q

from .models import Customer, Order, OrderItem, Product

MAX_ITEM_QUANTITY = 99


def is_valid_id(value):
    return str(value).isdigit() and len(str(value)) <= 12


def get_customer(user):
    customer, _ = Customer.objects.get_or_create(user=user, defaults={'email': user.email})
    return customer


def get_cart(customer):
    """Return the customer's cart, dropping rows for deleted products or with no quantity."""
    cart, _ = Order.objects.get_or_create(customer=customer, status=Order.Status.CART)
    cart.orderitem_set.filter(
        Q(product__isnull=True) | Q(quantity__isnull=True) | Q(quantity__lte=0),
    ).delete()
    return cart


def _read_cookie(request):
    """Return {product_id: quantity} from the guest cart cookie, ignoring anything invalid."""
    try:
        raw = json.loads(request.COOKIES.get('cart', '{}'))
    except ValueError:
        return {}
    if not isinstance(raw, dict):
        return {}

    cart = {}
    for product_id, entry in raw.items():
        quantity = entry.get('quantity') if isinstance(entry, dict) else None
        if is_valid_id(product_id) and type(quantity) is int and quantity > 0:
            cart[int(product_id)] = min(quantity, MAX_ITEM_QUANTITY)
    return cart


def cookieCart(request):
    quantities = _read_cookie(request)
    items = []
    order = {'get_cart_total': 0, 'get_cart_items': 0, 'shipping': False}

    for product in Product.objects.filter(pk__in=quantities).order_by('id'):
        quantity = quantities[product.pk]
        total = product.price * quantity
        order['get_cart_total'] += total
        order['get_cart_items'] += quantity
        if not product.digital:
            order['shipping'] = True
        items.append({
            'id': product.id,
            'product': {
                'id': product.id,
                'name': product.name,
                'price': product.price,
                'imageURL': product.imageURL,
            },
            'quantity': quantity,
            'digital': product.digital,
            'get_total': total,
        })

    return {'cartItems': order['get_cart_items'], 'order': order, 'items': items}


def cartData(request):
    if not request.user.is_authenticated:
        return cookieCart(request)

    order = get_cart(get_customer(request.user))
    items = order.orderitem_set.select_related('product').order_by('id')
    return {'cartItems': order.get_cart_items, 'order': order, 'items': items}


def merge_cookie_cart(request, user):
    """Move a guest's cart into their account when they log in."""
    quantities = _read_cookie(request)
    if not quantities:
        return
    cart = get_cart(get_customer(user))
    for product in Product.objects.filter(pk__in=quantities):
        item, _ = OrderItem.objects.get_or_create(order=cart, product=product)
        item.quantity = min((item.quantity or 0) + quantities[product.pk], MAX_ITEM_QUANTITY)
        item.save()


def cart_lines(request):
    """Return (product, quantity) pairs for the current cart, using live prices."""
    if request.user.is_authenticated:
        items = get_cart(get_customer(request.user)).orderitem_set.select_related('product')
        return [(item.product, item.quantity) for item in items]

    quantities = _read_cookie(request)
    return [(product, quantities[product.pk]) for product in Product.objects.filter(pk__in=quantities)]


def get_guest_customer(name, email):
    """Find or create a guest customer. Never attaches an order to a registered account."""
    customer = Customer.objects.filter(user__isnull=True, email__iexact=email).first()
    if customer is None:
        return Customer.objects.create(name=name, email=email)
    customer.name = name
    customer.save(update_fields=['name'])
    return customer
