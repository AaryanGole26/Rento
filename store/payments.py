"""Razorpay integration, and the order state changes shared with PayPal.

The browser is never trusted. An order only becomes paid after the server has
fetched the payment from Razorpay and confirmed it belongs to this order, for the
expected amount and currency, and has been captured. The checkout callback, the
webhook and the order page all go through settle(), so it is safe to run repeatedly.
"""

import hashlib
import hmac
import logging
import re
from decimal import ROUND_HALF_UP, Decimal

import requests
from django.conf import settings
from django.db import transaction

from .models import Order, OrderItem, Payment

logger = logging.getLogger(__name__)

API_BASE = 'https://api.razorpay.com/v1'
MINIMUM_AMOUNT = 100  # Razorpay rejects anything below 1.00 in the major unit
_PAYMENT_ID = re.compile(r'^pay_[A-Za-z0-9]+$')


class PaymentError(Exception):
    """The gateway could not be reached, refused a request, or returned bad data."""


def is_configured():
    if settings.PAYMENT_GATEWAY == 'paypal':
        return bool(settings.PAYPAL_CLIENT_ID)
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def to_minor_units(amount):
    return int((Decimal(amount) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def _request(method, path, payload=None):
    try:
        response = requests.request(
            method,
            f'{API_BASE}{path}',
            json=payload,
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise PaymentError(f'Could not reach Razorpay: {exc.__class__.__name__}') from exc

    if not response.ok:
        raise PaymentError(f'Razorpay returned {response.status_code} for {path}: {response.text[:300]}')
    try:
        return response.json()
    except ValueError as exc:
        raise PaymentError(f'Razorpay returned invalid JSON for {path}') from exc


def create_payment(order):
    """Register a pending order with the gateway and record the attempt."""
    if settings.PAYMENT_GATEWAY == 'paypal':
        from . import paypal
        return paypal.create_payment(order)

    amount = to_minor_units(order.get_cart_total)
    gateway_order = _request('POST', '/orders', {
        'amount': amount,
        'currency': settings.PAYMENT_CURRENCY,
        'receipt': f'rento-{order.pk}',
    })
    try:
        gateway_order_id = gateway_order['id']
    except (KeyError, TypeError) as exc:
        raise PaymentError('Razorpay did not return an order id') from exc
    return Payment.objects.create(
        order=order,
        provider=Payment.Provider.RAZORPAY,
        gateway_order_id=gateway_order_id,
        amount=amount,
        currency=settings.PAYMENT_CURRENCY,
    )


def _signature_matches(message, secret, signature):
    expected = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), str(signature).encode())


def verify_checkout_signature(gateway_order_id, gateway_payment_id, signature):
    # gateway_order_id must come from our own Payment row, not from the browser.
    message = f'{gateway_order_id}|{gateway_payment_id}'.encode()
    return _signature_matches(message, settings.RAZORPAY_KEY_SECRET, signature)


def verify_webhook_signature(body, signature):
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    return bool(secret and signature) and _signature_matches(body, secret, signature)


def settle(payment, gateway_payment_id):
    """Check a payment against Razorpay and update the order to match. Returns the Payment."""
    if not _PAYMENT_ID.match(gateway_payment_id or ''):
        raise PaymentError('Malformed payment id')

    details = _request('GET', f'/payments/{gateway_payment_id}')

    if (details.get('order_id') != payment.gateway_order_id
            or details.get('amount') != payment.amount
            or details.get('currency') != payment.currency):
        logger.error('Payment %s does not match order %s', gateway_payment_id, payment.gateway_order_id)
        return mark_failed(payment, 'Payment details did not match this order.')

    status = details.get('status')
    if status == 'authorized':
        status = _capture(payment, gateway_payment_id)

    if status == 'captured':
        return mark_paid(payment, gateway_payment_id)
    if status == 'failed':
        return mark_failed(payment, details.get('error_description') or 'The payment failed.')
    return payment  # still in progress, or refunded after being captured


def _capture(payment, gateway_payment_id):
    try:
        details = _request('POST', f'/payments/{gateway_payment_id}/capture', {
            'amount': payment.amount,
            'currency': payment.currency,
        })
    except PaymentError:
        # The webhook and the checkout callback can race. If the other one won,
        # this capture is refused but the payment is captured all the same.
        details = _request('GET', f'/payments/{gateway_payment_id}')
    return details.get('status')


def sync(payment):
    """Ask the gateway about an unresolved order, for when both callback and webhook were missed."""
    if payment.provider == Payment.Provider.PAYPAL:
        from . import paypal
        return paypal.sync(payment)

    response = _request('GET', f'/orders/{payment.gateway_order_id}/payments')
    attempts = response.get('items', [])
    for wanted in ('captured', 'authorized', 'failed'):
        for attempt in attempts:
            if attempt.get('status') == wanted:
                return settle(payment, attempt.get('id'))
    return payment


def mark_paid(payment, gateway_payment_id):
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related('order').get(pk=payment.pk)
        if payment.status == Payment.Status.PAID:
            return payment  # already handled, so the cart is not reduced twice

        was_in_review = payment.status == Payment.Status.REVIEW
        payment.status = Payment.Status.PAID
        payment.gateway_payment_id = gateway_payment_id
        payment.error_message = ''
        payment.save()

        order = payment.order
        order.status = Order.Status.PAID
        order.transaction_id = gateway_payment_id
        order.save()

        if not was_in_review:
            _remove_purchased_items(order)
    logger.info('Order %s paid with %s', order.pk, gateway_payment_id)
    return payment


def mark_review(payment, gateway_payment_id):
    """The browser reports a payment that can't be verified. An administrator decides."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related('order').get(pk=payment.pk)
        if payment.status in (Payment.Status.PAID, Payment.Status.REVIEW):
            return payment

        payment.status = Payment.Status.REVIEW
        payment.gateway_payment_id = gateway_payment_id
        payment.error_message = ''
        payment.save()

        order = payment.order
        order.status = Order.Status.REVIEW
        order.transaction_id = gateway_payment_id
        order.save()

        _remove_purchased_items(order)
    logger.info('Order %s awaiting review, PayPal capture %s', order.pk, gateway_payment_id)
    return payment


def reject_review(payment):
    """An administrator found no matching payment in their account."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related('order').get(pk=payment.pk)
        if payment.status != Payment.Status.REVIEW:
            return payment
        payment.status = Payment.Status.FAILED
        payment.error_message = 'We could not confirm this payment.'
        payment.save()
        payment.order.status = Order.Status.FAILED
        payment.order.save()
    return payment


def mark_failed(payment, message):
    with transaction.atomic():
        payment = Payment.objects.select_for_update().select_related('order').get(pk=payment.pk)
        if payment.status in (Payment.Status.PAID, Payment.Status.REVIEW):
            return payment  # a completed payment is never overwritten by a late failure

        payment.status = Payment.Status.FAILED
        payment.error_message = (message or '')[:255]
        payment.save()

        order = payment.order
        order.status = Order.Status.FAILED
        order.save()
    return payment


def _remove_purchased_items(order):
    """Take what was bought out of the customer's cart, keeping anything added since."""
    if order.customer_id is None:
        return
    cart_items = OrderItem.objects.filter(order__customer_id=order.customer_id, order__status=Order.Status.CART)
    for purchased in order.orderitem_set.all():
        cart_item = cart_items.filter(product_id=purchased.product_id).first()
        if cart_item is None:
            continue
        cart_item.quantity = (cart_item.quantity or 0) - (purchased.quantity or 0)
        if cart_item.quantity > 0:
            cart_item.save()
        else:
            cart_item.delete()
