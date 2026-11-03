import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.crypto import salted_hmac
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import payments, paypal
from .models import Order, OrderItem, Payment, Product, ShippingAddress
from .utils import (
    MAX_ITEM_QUANTITY,
    cart_lines,
    cartData,
    get_cart,
    get_customer,
    get_guest_customer,
    is_valid_id,
    merge_cookie_cart,
)

logger = logging.getLogger(__name__)

SESSION_ORDERS_KEY = 'order_ids'
SHIPPING_FIELDS = ('address', 'city', 'state', 'zipcode')


def _read_json(request):
    try:
        data = json.loads(request.body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# --- Accounts ---------------------------------------------------------------

@require_POST
def LogoutPage(request):
    logout(request)
    return redirect('store')


def SignupPage(request):
    if request.user.is_authenticated:
        return redirect('store')

    if request.method != 'POST':
        return render(request, 'store/signup.html', request.session.pop('signup_form', {}))

    username = request.POST.get('username', '').strip()
    email = request.POST.get('email', '').strip()
    password = request.POST.get('password1', '')
    confirmation = request.POST.get('password2', '')

    errors = []
    if not username:
        errors.append('Enter a username.')
    elif len(username) > 150:
        errors.append('That username is too long.')
    elif User.objects.filter(username__iexact=username).exists():
        errors.append('That username is already taken.')

    try:
        validate_email(email)
    except ValidationError:
        errors.append('Enter a valid email address.')
    else:
        if User.objects.filter(email__iexact=email).exists():
            errors.append('An account with that email already exists. Try logging in or resetting your password.')

    if password != confirmation:
        errors.append('The two passwords do not match.')
    else:
        try:
            validate_password(password, User(username=username, email=email))
        except ValidationError as exc:
            errors.extend(exc.messages)

    if errors:
        for error in errors:
            messages.error(request, error)
        # Redirecting keeps the password out of the browser's Back/Refresh resubmission.
        request.session['signup_form'] = {'username': username, 'email': email}
        return redirect('signup')

    User.objects.create_user(username, email, password)
    messages.success(request, 'Your account has been created. Please log in.')
    return redirect('login')


def _safe_next(request, url):
    allowed = url_has_allowed_host_and_scheme(
        url, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
    )
    return url if allowed else reverse('store')


def _login_url(next_url):
    url = reverse('login')
    return f'{url}?{urlencode({"next": next_url})}' if next_url else url


def _login_attempts_key(request, username):
    # Render's proxy appends the real client address as the last X-Forwarded-For entry.
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    address = forwarded.split(',')[-1].strip() or request.META.get('REMOTE_ADDR', '')
    return 'login-attempts:' + salted_hmac('login-attempts', f'{username.lower()}|{address}').hexdigest()


def LoginPage(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request, request.GET.get('next', '')))

    if request.method != 'POST':
        return render(request, 'store/login.html', {'next': request.GET.get('next', '')})

    username = request.POST.get('username', '').strip()
    next_url = request.POST.get('next', '')
    attempts_key = _login_attempts_key(request, username)

    if cache.get(attempts_key, 0) >= settings.LOGIN_MAX_FAILURES:
        messages.error(request, 'Too many failed attempts. Try again in 15 minutes, or reset your password.')
        return redirect(_login_url(next_url))

    user = authenticate(request, username=username, password=request.POST.get('pass', ''))
    if user is None:
        cache.add(attempts_key, 0, timeout=settings.LOGIN_LOCKOUT_SECONDS)
        cache.incr(attempts_key)
        messages.error(request, 'Username or password is incorrect.')
        return redirect(_login_url(next_url))

    cache.delete(attempts_key)
    login(request, user)
    if user.is_staff:
        request.session.set_expiry(settings.STAFF_SESSION_SECONDS)
    merge_cookie_cart(request, user)

    response = redirect(_safe_next(request, next_url))
    response.delete_cookie('cart', samesite='Lax')
    return response


class ForgotPasswordView(auth_views.PasswordResetView):
    template_name = 'store/forgot_password.html'
    email_template_name = 'store/emails/password_reset.txt'
    html_email_template_name = 'store/emails/password_reset.html'
    subject_template_name = 'store/emails/password_reset_subject.txt'
    success_url = reverse_lazy('login')

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('store')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # Same reply whether or not the account exists, so this page can't be
        # used to discover registered emails. Limited per address to stop mail floods.
        email = form.cleaned_data['email'].lower()
        cache_key = 'password-reset:' + salted_hmac('password-reset', email).hexdigest()
        if cache.add(cache_key, True, timeout=60):
            response = super().form_valid(form)
        else:
            response = redirect(self.success_url)
        messages.success(
            self.request,
            'If an account exists for that email, a reset link is on its way. '
            'Check your inbox and spam folder.',
        )
        return response


class ResetPasswordView(auth_views.PasswordResetConfirmView):
    template_name = 'store/reset_password.html'
    success_url = reverse_lazy('login')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Your password has been reset. Please log in.')
        return response


# --- Shop -------------------------------------------------------------------

def store(request):
    context = {'products': Product.objects.order_by('id'), 'cartItems': cartData(request)['cartItems']}
    return render(request, 'store/store.html', context)


def cart(request):
    return render(request, 'store/cart.html', cartData(request))


def checkout(request):
    data = cartData(request)
    if not data['items']:
        messages.info(request, 'Your cart is empty. Add something before checking out.')
        return redirect('cart')
    data['payments_enabled'] = payments.is_configured()
    data['gateway'] = settings.PAYMENT_GATEWAY
    data['paypal_client_id'] = settings.PAYPAL_CLIENT_ID
    data['paypal_verified'] = paypal.is_verified()
    data['payment_currency'] = settings.PAYMENT_CURRENCY
    return render(request, 'store/checkout.html', data)


@require_POST
def updateItem(request):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Login required.'}, status=401)  # guests use the cookie cart

    data = _read_json(request)
    action = data.get('action') if data else None
    product_id = data.get('productId') if data else None
    if action not in ('add', 'remove') or not is_valid_id(product_id):
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    product = Product.objects.filter(pk=product_id).first()
    if product is None:
        return JsonResponse({'error': 'That product is no longer available.'}, status=404)

    order = get_cart(get_customer(request.user))
    item, _ = OrderItem.objects.get_or_create(order=order, product=product)
    item.quantity = (item.quantity or 0) + (1 if action == 'add' else -1)
    if item.quantity <= 0:
        item.delete()
    else:
        item.quantity = min(item.quantity, MAX_ITEM_QUANTITY)
        item.save()

    return JsonResponse({'cartItems': order.get_cart_items})


def cart_data(request):
    return JsonResponse({'cartItems': cartData(request)['cartItems']})


# --- Payments ---------------------------------------------------------------

def _owns_order(request, order):
    if (request.user.is_authenticated and order.customer_id
            and order.customer.user_id == request.user.pk):
        return True
    return order.pk in request.session.get(SESSION_ORDERS_KEY, [])


def _can_view_order(request, order):
    return _owns_order(request, order) or (
        request.user.is_staff and request.user.has_perm('store.view_order')
    )


def _clean_checkout_details(request, data, lines):
    """Return (cleaned details, error message)."""
    details = {'name': '', 'email': '', 'shipping': {}}

    if not request.user.is_authenticated:
        form = data.get('form') if isinstance(data.get('form'), dict) else {}
        details['name'] = str(form.get('name') or '').strip()[:200]
        details['email'] = str(form.get('email') or '').strip()[:200]
        if not details['name']:
            return None, 'Please enter your name.'
        try:
            validate_email(details['email'])
        except ValidationError:
            return None, 'Please enter a valid email address.'

    if any(not product.digital for product, _ in lines):
        shipping = data.get('shipping') if isinstance(data.get('shipping'), dict) else {}
        for field in SHIPPING_FIELDS:
            details['shipping'][field] = str(shipping.get(field) or '').strip()[:200]
        if not all(details['shipping'].values()):
            return None, 'Please complete your shipping address.'

    return details, None


@require_POST
def create_payment(request):
    """Freeze the cart into a pending order and open a Razorpay order for it.

    The cart itself is left alone until payment succeeds, so a cancelled or failed
    attempt loses nothing, and editing the cart in another tab can't change what
    this attempt charges. The amount always comes from the database.
    """
    if not payments.is_configured():
        return JsonResponse({'error': 'Online payments are not available right now.'}, status=503)

    data = _read_json(request)
    if data is None:
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    lines = cart_lines(request)
    if not lines:
        return JsonResponse({'error': 'Your cart is empty.'}, status=400)

    details, error = _clean_checkout_details(request, data, lines)
    if error:
        return JsonResponse({'error': error}, status=400)

    total = sum(product.price * quantity for product, quantity in lines)
    if payments.to_minor_units(total) < payments.MINIMUM_AMOUNT:
        return JsonResponse({'error': 'The order total is below the minimum payment amount.'}, status=400)

    with transaction.atomic():
        if request.user.is_authenticated:
            customer = get_customer(request.user)
            contact_name = request.user.get_full_name() or request.user.username
            contact_email = request.user.email or customer.email
        else:
            customer = get_guest_customer(details['name'], details['email'])
            contact_name, contact_email = details['name'], details['email']

        order = Order.objects.create(customer=customer, status=Order.Status.PENDING)
        OrderItem.objects.bulk_create([
            OrderItem(order=order, product=product, quantity=quantity, price=product.price)
            for product, quantity in lines
        ])
        if details['shipping']:
            ShippingAddress.objects.create(customer=customer, order=order, **details['shipping'])

    try:
        payment = payments.create_payment(order)
    except payments.PaymentError:
        logger.exception('Could not start payment for order %s', order.pk)
        order.status = Order.Status.FAILED
        order.save(update_fields=['status'])
        return JsonResponse(
            {'error': 'Could not reach the payment provider. You have not been charged. Please try again.'},
            status=502,
        )

    request.session[SESSION_ORDERS_KEY] = [*request.session.get(SESSION_ORDERS_KEY, []), order.pk]

    return JsonResponse({
        'value': f'{payment.amount // 100}.{payment.amount % 100:02d}',
        'key': settings.RAZORPAY_KEY_ID,
        'amount': payment.amount,
        'currency': payment.currency,
        'order_id': payment.gateway_order_id,
        'name': 'Rento',
        'description': f'Order #{order.pk}',
        'prefill': {'name': contact_name, 'email': contact_email},
        'order_url': reverse('order_detail', args=[order.pk]),
    })


def _find_own_payment(request, data):
    payment = Payment.objects.select_related('order__customer').filter(
        gateway_order_id=str(data.get('razorpay_order_id') or data.get('order_id') or ''),
    ).first()
    if payment is None or not _owns_order(request, payment.order):
        return None
    return payment


@require_POST
def verify_payment(request):
    """Handle Razorpay Checkout's success callback."""
    data = _read_json(request) or {}
    payment = _find_own_payment(request, data)
    if payment is None:
        return JsonResponse({'error': 'Payment not found.'}, status=404)

    order_url = reverse('order_detail', args=[payment.order_id])

    if payment.provider == Payment.Provider.PAYPAL:
        try:
            payment = paypal.complete(payment, data)
        except payments.PaymentError:
            logger.exception('Could not complete PayPal order %s', payment.gateway_order_id)
            return JsonResponse({'status': 'pending', 'redirect_url': order_url}, status=202)
        return JsonResponse({'status': payment.status, 'redirect_url': order_url})

    gateway_payment_id = str(data.get('razorpay_payment_id') or '')
    signature = data.get('razorpay_signature') or ''

    if not payments.verify_checkout_signature(payment.gateway_order_id, gateway_payment_id, signature):
        logger.warning('Invalid checkout signature for %s', payment.gateway_order_id)
        return JsonResponse({'error': 'The payment could not be verified.', 'redirect_url': order_url}, status=400)

    try:
        payment = payments.settle(payment, gateway_payment_id)
    except payments.PaymentError:
        # The money may well have gone through. The order page and webhook will pick it up.
        logger.exception('Could not verify payment %s', gateway_payment_id)
        return JsonResponse({'status': 'pending', 'redirect_url': order_url}, status=202)

    return JsonResponse({'status': payment.status, 'redirect_url': order_url})


@require_POST
def payment_failed(request):
    """Record a failure the customer saw in the Razorpay window.

    Razorpay lets them retry in that same window, so a later success still wins.
    """
    data = _read_json(request) or {}
    payment = _find_own_payment(request, data)
    if payment is None:
        return JsonResponse({'error': 'Payment not found.'}, status=404)

    payments.mark_failed(payment, str(data.get('description') or 'The payment failed.'))
    return JsonResponse({'status': 'recorded'})


@csrf_exempt
@require_POST
def razorpay_webhook(request):
    """Server-to-server events, which cover customers who close the page after paying."""
    if not payments.verify_webhook_signature(request.body, request.headers.get('X-Razorpay-Signature')):
        return JsonResponse({'error': 'Invalid signature.'}, status=400)

    event = _read_json(request) or {}
    name = event.get('event', '')
    try:
        entity = event['payload']['payment']['entity']
        gateway_order_id, gateway_payment_id = entity['order_id'], entity['id']
    except (KeyError, TypeError):
        return JsonResponse({'status': 'ignored'})

    payment = Payment.objects.filter(gateway_order_id=gateway_order_id).first()
    if payment is None:
        return JsonResponse({'status': 'ignored'})  # not one of ours

    if name == 'payment.failed':
        payments.mark_failed(payment, entity.get('error_description') or 'The payment failed.')
    elif name in ('payment.authorized', 'payment.captured', 'order.paid'):
        try:
            payments.settle(payment, gateway_payment_id)
        except payments.PaymentError:
            logger.exception('Webhook could not verify payment %s', gateway_payment_id)
            return JsonResponse({'error': 'Try again.'}, status=503)  # Razorpay retries on non-2xx

    return JsonResponse({'status': 'ok'})


def order_detail(request, pk):
    order = get_object_or_404(Order.objects.select_related('customer'), pk=pk)
    if order.status == Order.Status.CART or not _can_view_order(request, order):
        raise Http404

    payment = Payment.objects.filter(order=order).first()
    if payment and order.status not in (Order.Status.PAID, Order.Status.REVIEW) and payments.is_configured():
        try:
            payment = payments.sync(payment)
            order.refresh_from_db()
        except payments.PaymentError:
            logger.exception('Could not check the status of order %s', order.pk)

    is_paid = order.status in (Order.Status.PAID, Order.Status.REVIEW)
    if is_paid:
        # A guest's cart lives in a cookie, so it can only be cleared from a response.
        request.COOKIES.pop('cart', None)

    response = render(request, 'store/order_detail.html', {
        'order': order,
        'payment': payment,
        'items': order.orderitem_set.select_related('product'),
        'shipping_address': order.shippingaddress_set.first(),
        'cartItems': cartData(request)['cartItems'],
    })
    if is_paid:
        response.delete_cookie('cart', samesite='Lax')
    return response
