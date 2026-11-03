"""PayPal integration, in one of two modes depending on PAYPAL_CLIENT_SECRET.

With the secret, the server creates and captures each order through PayPal's Orders
API and checks the result, so orders are marked paid automatically.

Without it, the server has no way to ask PayPal about a payment. The PayPal buttons
still take the payment in the browser, but the browser's report can't be trusted, so
the order waits in "review" until an administrator confirms it against their PayPal
account. It is never marked paid on the browser's word.
"""

import logging
import re
import uuid

import requests
from django.conf import settings
from django.core.cache import cache

from . import payments
from .models import Payment
from .payments import PaymentError

logger = logging.getLogger(__name__)

API_BASES = {'sandbox': 'https://api-m.sandbox.paypal.com', 'live': 'https://api-m.paypal.com'}
_CAPTURE_ID = re.compile(r'^[A-Za-z0-9-]{5,64}$')
LOCAL_PREFIX = 'local-'


def is_verified():
    return bool(settings.PAYPAL_CLIENT_ID and settings.PAYPAL_CLIENT_SECRET)


def _api_base():
    return API_BASES.get(settings.PAYPAL_MODE, API_BASES['sandbox'])


def _access_token():
    token = cache.get('paypal-access-token')
    if token:
        return token
    try:
        response = requests.post(
            f'{_api_base()}/v1/oauth2/token',
            data={'grant_type': 'client_credentials'},
            auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
            timeout=15,
        )
    except requests.RequestException as exc:
        raise PaymentError(f'Could not reach PayPal: {exc.__class__.__name__}') from exc
    if not response.ok:
        raise PaymentError(f'PayPal refused the credentials ({response.status_code})')
    data = response.json()
    cache.set('paypal-access-token', data['access_token'], timeout=max(data.get('expires_in', 300) - 60, 60))
    return data['access_token']


def _request(method, path, payload=None, request_id=None):
    headers = {'Authorization': f'Bearer {_access_token()}', 'Prefer': 'return=representation'}
    if request_id:
        headers['PayPal-Request-Id'] = request_id
    try:
        response = requests.request(method, f'{_api_base()}{path}', json=payload, headers=headers, timeout=20)
    except requests.RequestException as exc:
        raise PaymentError(f'Could not reach PayPal: {exc.__class__.__name__}') from exc
    if not response.ok:
        raise PaymentError(f'PayPal returned {response.status_code} for {path}: {response.text[:300]}')
    try:
        return response.json()
    except ValueError as exc:
        raise PaymentError(f'PayPal returned invalid JSON for {path}') from exc


def _value(amount):
    return f'{amount // 100}.{amount % 100:02d}'


def create_payment(order):
    amount = payments.to_minor_units(order.get_cart_total)
    currency = settings.PAYMENT_CURRENCY

    if not is_verified():
        gateway_order_id = f'{LOCAL_PREFIX}{uuid.uuid4().hex}'
    else:
        created = _request('POST', '/v2/checkout/orders', {
            'intent': 'CAPTURE',
            'purchase_units': [{
                'custom_id': str(order.pk),
                'amount': {'currency_code': currency, 'value': _value(amount)},
            }],
        }, request_id=f'rento-create-{order.pk}')
        gateway_order_id = created.get('id')
        if not gateway_order_id:
            raise PaymentError('PayPal did not return an order id')

    return Payment.objects.create(
        order=order,
        provider=Payment.Provider.PAYPAL,
        gateway_order_id=gateway_order_id,
        amount=amount,
        currency=currency,
    )


def _settle_from_order(payment, details):
    """Update the order from PayPal's own record of it."""
    if details.get('status') != 'COMPLETED':
        return payment
    try:
        unit = details['purchase_units'][0]
        capture = unit['payments']['captures'][0]
        amount = capture['amount']
    except (KeyError, IndexError, TypeError) as exc:
        raise PaymentError('PayPal returned an incomplete order') from exc

    if (capture.get('status') != 'COMPLETED'
            or amount.get('currency_code') != payment.currency
            or amount.get('value') != _value(payment.amount)
            or str(unit.get('custom_id', payment.order_id)) != str(payment.order_id)):
        logger.error('PayPal order %s does not match order %s', payment.gateway_order_id, payment.order_id)
        return payments.mark_failed(payment, 'Payment details did not match this order.')
    return payments.mark_paid(payment, capture.get('id', ''))


def _capture(payment):
    return _request(
        'POST', f'/v2/checkout/orders/{payment.gateway_order_id}/capture', {},
        request_id=f'rento-capture-{payment.pk}',
    )


def complete(payment, data):
    """Handle the browser saying the customer approved the payment."""
    if payment.gateway_order_id.startswith(LOCAL_PREFIX):
        capture_id = str(data.get('capture_id') or '')
        if not _CAPTURE_ID.match(capture_id):
            raise PaymentError('Malformed PayPal capture id')
        return payments.mark_review(payment, capture_id)
    try:
        return _settle_from_order(payment, _capture(payment))
    except PaymentError:
        # The capture may have gone through already, so PayPal's record decides.
        return sync(payment)


def sync(payment):
    """Ask PayPal about an unresolved order. Unverifiable without the secret."""
    if payment.gateway_order_id.startswith(LOCAL_PREFIX) or not is_verified():
        return payment
    details = _request('GET', f'/v2/checkout/orders/{payment.gateway_order_id}')
    if details.get('status') == 'VOIDED':
        return payments.mark_failed(payment, 'The payment was cancelled.')
    return _settle_from_order(payment, details)
