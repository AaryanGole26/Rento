import hashlib
import hmac
import json
import re
from decimal import Decimal
from unittest import mock

import requests
from django.contrib.auth.models import Group, Permission, User
from django.core import mail
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import payments, paypal
from .email_backends import BrevoEmailBackend
from .models import Customer, Order, OrderItem, Payment, Product, ShippingAddress

STRONG_PASSWORD = 'correct-horse-battery-9'
KEY_SECRET = 'test_key_secret'
WEBHOOK_SECRET = 'test_webhook_secret'


class FakeRazorpay:
    """Stands in for payments._request, behaving like the Razorpay REST API."""

    def __init__(self):
        self.orders = {}
        self.payments = {}
        self.calls = []
        self.down = False

    def add_payment(self, gateway_order_id, status='captured', amount=None, currency='INR', error=None):
        payment_id = f'pay_Test{len(self.payments) + 1}'
        self.payments[payment_id] = {
            'id': payment_id,
            'order_id': gateway_order_id,
            'amount': self.orders[gateway_order_id]['amount'] if amount is None else amount,
            'currency': currency,
            'status': status,
            'error_description': error,
        }
        return payment_id

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if self.down:
            raise payments.PaymentError('Could not reach Razorpay: ConnectionError')

        if method == 'POST' and path == '/orders':
            order_id = f'order_Test{len(self.orders) + 1}'
            self.orders[order_id] = {'id': order_id, **payload}
            return self.orders[order_id]

        match = re.fullmatch(r'/orders/(\w+)/payments', path)
        if match:
            return {'items': [p for p in self.payments.values() if p['order_id'] == match.group(1)]}

        match = re.fullmatch(r'/payments/(\w+)/capture', path)
        if match:
            payment = self.payments[match.group(1)]
            if payment['status'] != 'authorized':
                raise payments.PaymentError('This payment has already been captured')
            payment['status'] = 'captured'
            return payment

        match = re.fullmatch(r'/payments/(\w+)', path)
        if match:
            if match.group(1) not in self.payments:
                raise payments.PaymentError('Razorpay returned 400')
            return self.payments[match.group(1)]

        raise AssertionError(f'Unexpected Razorpay call: {method} {path}')


def checkout_signature(gateway_order_id, gateway_payment_id, secret=KEY_SECRET):
    message = f'{gateway_order_id}|{gateway_payment_id}'.encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def post_json(client, url, data):
    return client.post(url, json.dumps(data), content_type='application/json')


SHIPPING = {'address': '1 Main St', 'city': 'Mumbai', 'state': 'MH', 'zipcode': '400001'}


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    DEFAULT_FROM_EMAIL='Rento <noreply@example.com>',
    ALLOWED_HOSTS=['testserver', 'rento.onrender.com'],
)
class ForgotPasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('asha', 'asha@example.com', 'old-password-123!')

    def request_link(self, email='asha@example.com', **kwargs):
        return self.client.post(reverse('forgot_password'), {'email': email}, follow=True, **kwargs)

    def link_from_email(self):
        return re.search(r'https?://\S+', mail.outbox[-1].body).group(0)

    def path_from_email(self):
        return re.sub(r'^https?://[^/]+', '', self.link_from_email())

    def test_full_reset_flow(self):
        response = self.request_link()
        self.assertContains(response, 'reset link is on its way')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['asha@example.com'])
        self.assertEqual(mail.outbox[0].from_email, 'Rento <noreply@example.com>')

        form_page = self.client.get(self.path_from_email(), follow=True)
        self.assertTrue(form_page.context['validlink'])

        response = self.client.post(
            form_page.redirect_chain[-1][0],
            {'new_password1': STRONG_PASSWORD, 'new_password2': STRONG_PASSWORD},
            follow=True,
        )
        self.assertContains(response, 'Your password has been reset')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(STRONG_PASSWORD))

        logged_in = self.client.post(reverse('login'), {'username': 'asha', 'pass': STRONG_PASSWORD})
        self.assertRedirects(logged_in, reverse('store'))

    def test_link_only_works_once(self):
        self.request_link()
        path = self.path_from_email()
        form_page = self.client.get(path, follow=True)
        self.client.post(
            form_page.redirect_chain[-1][0],
            {'new_password1': STRONG_PASSWORD, 'new_password2': STRONG_PASSWORD},
        )
        response = Client().get(path, follow=True)
        self.assertFalse(response.context['validlink'])
        self.assertContains(response, 'Link Expired')

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_expired_link_is_rejected(self):
        self.request_link()
        response = self.client.get(self.path_from_email(), follow=True)
        self.assertFalse(response.context['validlink'])

    def test_garbage_link_is_rejected(self):
        response = self.client.get(
            reverse('password_reset_confirm', args=['bad', 'bad-token']), follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['validlink'])

    def test_unknown_email_gets_same_reply_and_no_mail(self):
        known = self.request_link().content.decode()
        cache.clear()
        unknown = self.request_link('nobody@example.com').content.decode()
        self.assertIn('reset link is on its way', unknown)
        self.assertEqual(len(mail.outbox), 1)
        strip_token = lambda html: re.sub(r'value="[^"]{40,}"', '', html)
        self.assertEqual(strip_token(known), strip_token(unknown))

    def test_email_match_ignores_case(self):
        self.request_link('ASHA@EXAMPLE.COM')
        self.assertEqual(len(mail.outbox), 1)

    def test_duplicate_emails_do_not_crash(self):
        User.objects.create_user('asha2', 'asha@example.com', 'another-pass-123!')
        response = self.request_link()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 2)

    def test_weak_password_is_rejected(self):
        self.request_link()
        form_page = self.client.get(self.path_from_email(), follow=True)
        response = self.client.post(
            form_page.redirect_chain[-1][0], {'new_password1': '123', 'new_password2': '123'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'too short')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('old-password-123!'))

    def test_mismatched_passwords_are_rejected(self):
        self.request_link()
        form_page = self.client.get(self.path_from_email(), follow=True)
        response = self.client.post(
            form_page.redirect_chain[-1][0],
            {'new_password1': STRONG_PASSWORD, 'new_password2': STRONG_PASSWORD + 'x'},
        )
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('old-password-123!'))

    def test_mail_server_failure_is_not_a_500(self):
        with mock.patch('django.core.mail.EmailMultiAlternatives.send', side_effect=OSError('SMTP blocked')):
            with self.assertLogs('django.contrib.auth', level='ERROR'):
                response = self.request_link()
        self.assertEqual(response.status_code, 200)

    def test_repeat_requests_are_throttled(self):
        self.request_link()
        response = self.request_link()
        self.assertContains(response, 'reset link is on its way')
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'))
    def test_link_uses_public_https_host_behind_proxy(self):
        self.client.post(
            reverse('forgot_password'), {'email': 'asha@example.com'},
            HTTP_HOST='rento.onrender.com', HTTP_X_FORWARDED_PROTO='https',
        )
        self.assertTrue(self.link_from_email().startswith('https://rento.onrender.com/reset-password/'))

    def test_forged_host_header_is_refused(self):
        response = self.client.post(
            reverse('forgot_password'), {'email': 'asha@example.com'}, HTTP_HOST='evil.example.com',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)


class ProxyCsrfTests(TestCase):
    """Every browser POST on Render failed this way before SECURE_PROXY_SSL_HEADER."""

    @override_settings(
        ALLOWED_HOSTS=['rento.onrender.com'],
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
    )
    def test_post_through_tls_proxy_passes_csrf(self):
        client = Client(
            enforce_csrf_checks=True,
            HTTP_HOST='rento.onrender.com',
            HTTP_X_FORWARDED_PROTO='https',
        )
        token = client.get(reverse('login')).cookies['csrftoken'].value
        response = client.post(
            reverse('login'),
            {'username': 'x', 'pass': 'y', 'csrfmiddlewaretoken': token},
            HTTP_ORIGIN='https://rento.onrender.com',
            HTTP_REFERER='https://rento.onrender.com/login/',
        )
        self.assertEqual(response.status_code, 302)  # past CSRF; a bad login redirects back


class BrevoBackendTests(TestCase):
    @override_settings(BREVO_API_KEY='brevo-key', EMAIL_TIMEOUT=10)
    def test_sends_through_http_api(self):
        message = mail.EmailMultiAlternatives('Hi', 'Plain', 'Rento <from@example.com>', ['to@example.com'])
        message.attach_alternative('<b>Rich</b>', 'text/html')
        with mock.patch('store.email_backends.requests.post') as post:
            self.assertEqual(BrevoEmailBackend().send_messages([message]), 1)

        url = post.call_args.args[0]
        kwargs = post.call_args.kwargs
        self.assertEqual(url, 'https://api.brevo.com/v3/smtp/email')
        self.assertEqual(kwargs['headers']['api-key'], 'brevo-key')
        self.assertEqual(kwargs['timeout'], 10)
        self.assertEqual(kwargs['json']['sender'], {'email': 'from@example.com', 'name': 'Rento'})
        self.assertEqual(kwargs['json']['to'], [{'email': 'to@example.com'}])
        self.assertEqual(kwargs['json']['textContent'], 'Plain')
        self.assertEqual(kwargs['json']['htmlContent'], '<b>Rich</b>')

    @override_settings(BREVO_API_KEY='brevo-key', EMAIL_TIMEOUT=10)
    def test_api_errors_are_raised(self):
        message = mail.EmailMessage('Hi', 'Plain', 'from@example.com', ['to@example.com'])
        with mock.patch('store.email_backends.requests.post', side_effect=requests.ConnectionError):
            with self.assertRaises(requests.ConnectionError):
                BrevoEmailBackend().send_messages([message])
            self.assertEqual(BrevoEmailBackend(fail_silently=True).send_messages([message]), 0)


class AccountTests(TestCase):
    def setUp(self):
        cache.clear()

    def signup(self, **overrides):
        data = {
            'username': 'newuser', 'email': 'new@example.com',
            'password1': STRONG_PASSWORD, 'password2': STRONG_PASSWORD,
        }
        data.update(overrides)
        return self.client.post(reverse('signup'), data, follow=True)

    def test_signup_creates_user_with_customer_email(self):
        response = self.signup()
        self.assertContains(response, 'Your account has been created')
        customer = User.objects.get(username='newuser').customer
        self.assertEqual(customer.email, 'new@example.com')
        self.assertEqual(Customer.objects.count(), 1)

    def test_duplicate_username_and_email_show_errors(self):
        User.objects.create_user('newuser', 'taken@example.com', 'x')
        self.assertContains(self.signup(), 'already taken')
        self.assertContains(self.signup(username='other', email='TAKEN@example.com'), 'already exists')
        self.assertEqual(User.objects.count(), 1)

    def test_invalid_signups_show_errors(self):
        self.assertContains(self.signup(username=''), 'Enter a username')
        self.assertContains(self.signup(email='nope'), 'valid email')
        self.assertContains(self.signup(password2='different'), 'do not match')
        self.assertContains(self.signup(password1='123', password2='123'), 'too short')
        self.assertEqual(User.objects.count(), 0)

    def test_bad_login_shows_error(self):
        response = self.client.post(reverse('login'), {'username': 'x', 'pass': 'y'}, follow=True)
        self.assertContains(response, 'incorrect')

    def test_login_follows_only_local_next(self):
        User.objects.create_user('u', 'u@example.com', STRONG_PASSWORD)
        data = {'username': 'u', 'pass': STRONG_PASSWORD}
        self.assertRedirects(
            self.client.post(reverse('login'), {**data, 'next': '/cart/'}), '/cart/',
        )
        self.client.logout()
        response = self.client.post(reverse('login'), {**data, 'next': 'https://evil.example.com/'})
        self.assertRedirects(response, reverse('store'))

    def test_logout_requires_post(self):
        user = User.objects.create_user('u', 'u@example.com', STRONG_PASSWORD)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertIn('_auth_user_id', self.client.session)
        self.client.post(reverse('logout'))
        self.assertNotIn('_auth_user_id', self.client.session)


class ShopTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Drill', price=Decimal('10.50'))
        self.user = User.objects.create_user('u', 'u@example.com', STRONG_PASSWORD)

    def test_public_pages_load_for_guests(self):
        for name in ('store', 'cart', 'login', 'signup', 'forgot_password'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_public_pages_load_when_logged_in(self):
        self.client.force_login(self.user)
        for name in ('store', 'cart'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_pages_are_well_formed(self):
        html = self.client.get(reverse('store')).content.decode()
        self.assertRegex(html, r'(?i)^\s*<!doctype html>\s*<html')
        self.assertNotIn('<script>\n  <script>', html)
        self.assertNotIn('stackpath', html)

    def test_pages_load_no_third_party_css_or_scripts(self):
        for name in ('store', 'cart', 'login', 'signup', 'forgot_password'):
            html = self.client.get(reverse(name)).content.decode()
            self.assertNotRegex(html, r'<(script|link)[^>]+(src|href)="https?://', name)

    def test_services_describe_the_rental_business(self):
        html = self.client.get(reverse('store')).content.decode()
        self.assertIn('Equipment Rental', html)
        for wrong in ('Web Development', 'AI Solutions', 'UI/UX Design', 'textbooks', 'software'):
            self.assertNotIn(wrong, html)

    def test_product_images_can_be_cached(self):
        Product.objects.filter(pk=self.product.pk).update(image='camera.jpg')
        response = self.client.get('/images/camera.jpg')
        self.assertIn('max-age=86400', response['Cache-Control'])
        b''.join(response.streaming_content)

    def test_home_sections_only_appear_on_the_home_page(self):
        self.assertContains(self.client.get(reverse('store')), 'Meet Our Team')
        self.assertNotContains(self.client.get(reverse('cart')), 'Meet Our Team')

    def test_empty_cart_shows_empty_state_without_checkout_link(self):
        response = self.client.get(reverse('cart'))
        self.assertContains(response, 'Your cart is empty')
        self.assertNotContains(response, 'Proceed to Checkout')

    def test_sign_in_screens_are_locked_to_brand_theme(self):
        for name in ('login', 'signup', 'forgot_password'):
            self.assertContains(self.client.get(reverse(name)), 'data-lock-theme', msg_prefix=name)

    def test_branded_error_pages(self):
        self.assertContains(self.client.get('/no-such-page/'), 'Page Not Found', status_code=404)
        self.assertIn('Something Went Wrong', render_to_string('500.html'))

    def test_shop_page_sets_csrf_cookie_for_guests(self):
        self.assertIn('csrftoken', self.client.get(reverse('store')).cookies)

    def test_product_images_are_served_with_debug_off(self):
        Product.objects.filter(pk=self.product.pk).update(image='camera.jpg')
        self.product.refresh_from_db()
        self.assertEqual(self.product.imageURL, '/images/camera.jpg')
        response = self.client.get(self.product.imageURL)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(b''.join(response.streaming_content))

    def test_image_route_blocks_path_traversal(self):
        self.assertIn(self.client.get('/images/../../manage.py').status_code, (400, 404))

    def test_product_without_image_uses_placeholder(self):
        self.assertIn('placeholder.png', self.product.imageURL)

    def test_update_item_add_remove(self):
        self.client.force_login(self.user)
        data = {'productId': self.product.pk, 'action': 'add'}
        self.assertEqual(post_json(self.client, reverse('update_item'), data).json(), {'cartItems': 1})
        self.assertEqual(post_json(self.client, reverse('update_item'), data).json(), {'cartItems': 2})
        self.assertEqual(self.client.get(reverse('cart_data')).json(), {'cartItems': 2})
        data['action'] = 'remove'
        post_json(self.client, reverse('update_item'), data)
        self.assertEqual(post_json(self.client, reverse('update_item'), data).json(), {'cartItems': 0})
        self.assertFalse(OrderItem.objects.exists())

    def test_update_item_rejects_bad_input(self):
        url = reverse('update_item')
        self.assertEqual(post_json(self.client, url, {'productId': self.product.pk, 'action': 'add'}).status_code, 401)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, 'not json', content_type='application/json').status_code, 400)
        self.assertEqual(post_json(self.client, url, {'productId': self.product.pk, 'action': 'x'}).status_code, 400)
        self.assertEqual(post_json(self.client, url, {'productId': 'abc', 'action': 'add'}).status_code, 400)
        self.assertEqual(post_json(self.client, url, {'productId': 99999, 'action': 'add'}).status_code, 404)

    def test_guest_cookie_cart_totals(self):
        self.client.cookies['cart'] = json.dumps({str(self.product.pk): {'quantity': 3}})
        response = self.client.get(reverse('cart'))
        self.assertContains(response, 'Drill')
        self.assertContains(response, '31.50')
        self.assertEqual(self.client.get(reverse('cart_data')).json(), {'cartItems': 3})

    def test_malformed_cookies_are_ignored(self):
        for value in ('not json', '[]', '{"x":{"quantity":1}}', '{"1":{"quantity":"a"}}',
                      '{"1":{"quantity":-5}}', '{"1":5}', '{"99999999999999999999":{"quantity":1}}'):
            self.client.cookies['cart'] = value
            self.assertEqual(self.client.get(reverse('cart_data')).json(), {'cartItems': 0}, value)

    def test_guest_cart_moves_into_account_on_login(self):
        self.client.cookies['cart'] = json.dumps({str(self.product.pk): {'quantity': 2}})
        response = self.client.post(reverse('login'), {'username': 'u', 'pass': STRONG_PASSWORD})
        self.assertEqual(response.cookies['cart'].value, '')
        self.assertEqual(self.user.customer.order_set.get(status='cart').get_cart_items, 2)

    def test_user_without_customer_record_works(self):
        Customer.objects.filter(user=self.user).delete()
        self.user.refresh_from_db()
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('cart_data')).json(), {'cartItems': 0})
        self.assertEqual(self.client.get(reverse('store')).status_code, 200)

    def test_deleted_product_does_not_break_cart(self):
        self.client.force_login(self.user)
        doomed = Product.objects.create(name='Doomed', price=5)
        post_json(self.client, reverse('update_item'), {'productId': doomed.pk, 'action': 'add'})
        doomed.delete()
        self.assertEqual(self.client.get(reverse('cart')).status_code, 200)
        self.assertEqual(self.client.get(reverse('cart_data')).json(), {'cartItems': 0})

    def test_one_cart_per_customer_is_enforced(self):
        customer = self.user.customer
        Order.objects.create(customer=customer)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(customer=customer)

    def test_empty_checkout_redirects_to_cart(self):
        self.assertRedirects(self.client.get(reverse('checkout')), reverse('cart'))

    def test_old_unverified_payment_routes_are_gone(self):
        self.assertEqual(post_json(self.client, '/process_order/', {}).status_code, 404)
        self.assertEqual(self.client.get('/paymentgateway/').status_code, 404)


@override_settings(
    RAZORPAY_KEY_ID='rzp_test_key',
    RAZORPAY_KEY_SECRET=KEY_SECRET,
    RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET,
    PAYMENT_CURRENCY='INR',
    PAYMENT_GATEWAY='razorpay',
)
class PaymentTestCase(TestCase):
    def setUp(self):
        self.gateway = FakeRazorpay()
        patcher = mock.patch('store.payments._request', self.gateway)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.product = Product.objects.create(name='Drill', price=Decimal('10.50'))
        self.user = User.objects.create_user('u', 'u@example.com', STRONG_PASSWORD)
        self.client.force_login(self.user)
        self.cart = Order.objects.create(customer=self.user.customer)
        OrderItem.objects.create(order=self.cart, product=self.product, quantity=2)

    def start(self, client=None, **overrides):
        body = {'form': {}, 'shipping': SHIPPING, **overrides}
        response = post_json(client or self.client, reverse('payment_create'), body)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def finish(self, session, payment_id, client=None, signature=None):
        return post_json(client or self.client, reverse('payment_verify'), {
            'razorpay_order_id': session['order_id'],
            'razorpay_payment_id': payment_id,
            'razorpay_signature': signature or checkout_signature(session['order_id'], payment_id),
        })

    def webhook(self, event, entity, secret=WEBHOOK_SECRET):
        body = json.dumps({'event': event, 'payload': {'payment': {'entity': entity}}}).encode()
        signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return self.client.post(
            reverse('payment_webhook'), body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature,
        )

    def pending_order(self):
        return Order.objects.exclude(status=Order.Status.CART).latest('pk')


class PaymentInitiationTests(PaymentTestCase):
    def test_creates_gateway_order_from_database_prices(self):
        session = self.start(form={'total': '0.01'})
        self.assertEqual(session['amount'], 2100)
        self.assertEqual(session['currency'], 'INR')
        self.assertEqual(session['key'], 'rzp_test_key')
        self.assertEqual(self.gateway.orders[session['order_id']]['amount'], 2100)

        order = self.pending_order()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.get_cart_total, Decimal('21.00'))
        self.assertEqual(ShippingAddress.objects.get(order=order).city, 'Mumbai')

    def test_cart_is_untouched_until_paid(self):
        self.start()
        self.cart.refresh_from_db()
        self.assertEqual(self.cart.status, Order.Status.CART)
        self.assertEqual(self.cart.get_cart_items, 2)

    def test_checkout_page_uses_razorpay(self):
        response = self.client.get(reverse('checkout'))
        self.assertContains(response, 'checkout.razorpay.com/v1/checkout.js')
        self.assertContains(response, '₹21.00')
        self.assertNotContains(response, 'paypal')

    def test_empty_cart_is_refused(self):
        self.cart.orderitem_set.all().delete()
        response = post_json(self.client, reverse('payment_create'), {'shipping': SHIPPING})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.gateway.calls, [])

    def test_incomplete_address_is_refused(self):
        response = post_json(self.client, reverse('payment_create'), {'shipping': {**SHIPPING, 'city': '  '}})
        self.assertEqual(response.status_code, 400)
        self.assertIn('shipping', response.json()['error'])
        self.assertEqual(self.gateway.calls, [])

    def test_digital_only_order_needs_no_address(self):
        Product.objects.filter(pk=self.product.pk).update(digital=True)
        self.start(shipping={})
        self.assertFalse(ShippingAddress.objects.exists())

    def test_amount_below_gateway_minimum_is_refused(self):
        Product.objects.filter(pk=self.product.pk).update(price=Decimal('0.10'))
        response = post_json(self.client, reverse('payment_create'), {'shipping': SHIPPING})
        self.assertEqual(response.status_code, 400)
        self.assertIn('minimum', response.json()['error'])

    def test_gateway_outage_reports_cleanly(self):
        self.gateway.down = True
        with self.assertLogs('store.views', level='ERROR'):
            response = post_json(self.client, reverse('payment_create'), {'shipping': SHIPPING})
        self.assertEqual(response.status_code, 502)
        self.assertIn('not been charged', response.json()['error'])
        self.assertEqual(self.pending_order().status, Order.Status.FAILED)
        self.assertEqual(self.cart.get_cart_items, 2)

    def test_malformed_body_is_refused(self):
        response = self.client.post(reverse('payment_create'), 'nope', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_get_is_refused(self):
        for name in ('payment_create', 'payment_verify', 'payment_failed', 'payment_webhook'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405, name)

    def test_csrf_is_enforced_on_payment_create(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(post_json(client, reverse('payment_create'), {'shipping': SHIPPING}).status_code, 403)

    @override_settings(RAZORPAY_KEY_ID='', RAZORPAY_KEY_SECRET='')
    def test_without_keys_payment_is_unavailable_not_faked(self):
        response = post_json(self.client, reverse('payment_create'), {'shipping': SHIPPING})
        self.assertEqual(response.status_code, 503)
        page = self.client.get(reverse('checkout'))
        self.assertContains(page, 'not available right now')
        self.assertNotContains(page, 'checkout.razorpay.com')
        self.assertFalse(Order.objects.exclude(status='cart').exists())


class PaymentVerificationTests(PaymentTestCase):
    def test_successful_payment(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'])
        response = self.finish(session, payment_id)

        self.assertEqual(response.json()['status'], 'paid')
        order = self.pending_order()
        self.assertEqual(order.status, Order.Status.PAID)
        self.assertTrue(order.complete)
        self.assertEqual(order.transaction_id, payment_id)
        self.assertEqual(Payment.objects.get(order=order).gateway_payment_id, payment_id)
        self.assertEqual(self.client.get(reverse('cart_data')).json(), {'cartItems': 0})

        page = self.client.get(response.json()['redirect_url'])
        self.assertContains(page, 'Payment successful')
        self.assertContains(page, payment_id)

    def test_forged_signature_is_rejected(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'])
        with self.assertLogs('store.views', level='WARNING'):
            response = self.finish(session, payment_id, signature='0' * 64)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.pending_order().status, Order.Status.PENDING)
        self.assertEqual(self.cart.get_cart_items, 2)

    def test_signature_from_wrong_secret_is_rejected(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'])
        signature = checkout_signature(session['order_id'], payment_id, secret='attacker')
        with self.assertLogs('store.views', level='WARNING'):
            self.assertEqual(self.finish(session, payment_id, signature=signature).status_code, 400)

    def test_non_ascii_signature_does_not_crash(self):
        session = self.start()
        with self.assertLogs('store.views', level='WARNING'):
            self.assertEqual(self.finish(session, 'pay_x', signature='é' * 64).status_code, 400)

    def test_cheaper_payment_cannot_settle_order(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'], amount=100)
        with self.assertLogs('store.payments', level='ERROR'):
            response = self.finish(session, payment_id)
        self.assertEqual(response.json()['status'], 'failed')
        self.assertEqual(self.pending_order().status, Order.Status.FAILED)
        self.assertEqual(self.cart.get_cart_items, 2)

    def test_wrong_currency_cannot_settle_order(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'], currency='USD')
        with self.assertLogs('store.payments', level='ERROR'):
            self.assertEqual(self.finish(session, payment_id).json()['status'], 'failed')

    def test_payment_from_another_order_cannot_be_reused(self):
        first = self.start()
        reused = self.gateway.add_payment(first['order_id'])
        self.finish(first, reused)
        OrderItem.objects.create(order=self.cart, product=self.product, quantity=2)

        second = self.start()
        with self.assertLogs('store.payments', level='ERROR'):
            response = self.finish(second, reused)
        self.assertEqual(response.json()['status'], 'failed')
        self.assertEqual(Order.objects.get(payment__gateway_order_id=second['order_id']).status, 'failed')

    def test_authorized_payment_is_captured(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'], status='authorized')
        self.assertEqual(self.finish(session, payment_id).json()['status'], 'paid')
        self.assertEqual(self.gateway.payments[payment_id]['status'], 'captured')
        self.assertIn(('POST', f'/payments/{payment_id}/capture', {'amount': 2100, 'currency': 'INR'}), self.gateway.calls)

    def test_capture_race_with_webhook_still_succeeds(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'], status='authorized')
        original = self.gateway.__call__

        def racing(method, path, payload=None):
            if path.endswith('/capture'):
                self.gateway.payments[payment_id]['status'] = 'captured'
            return original(method, path, payload)

        with mock.patch('store.payments._request', racing):
            self.assertEqual(self.finish(session, payment_id).json()['status'], 'paid')

    def test_payment_still_processing_stays_pending(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'], status='created')
        self.assertEqual(self.finish(session, payment_id).json()['status'], 'created')
        self.assertEqual(self.pending_order().status, Order.Status.PENDING)

    def test_gateway_outage_during_verification_keeps_order_pending(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'])
        self.gateway.down = True
        with self.assertLogs('store.views', level='ERROR'):
            response = self.finish(session, payment_id)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.pending_order().status, Order.Status.PENDING)

        self.gateway.down = False
        self.assertContains(self.client.get(response.json()['redirect_url']), 'Payment successful')

    def test_verifying_twice_only_reduces_cart_once(self):
        OrderItem.objects.filter(order=self.cart).update(quantity=2)
        session = self.start()
        OrderItem.objects.filter(order=self.cart).update(quantity=5)  # more added in another tab
        payment_id = self.gateway.add_payment(session['order_id'])
        self.finish(session, payment_id)
        self.finish(session, payment_id)
        self.assertEqual(self.cart.get_cart_items, 3)

    def test_items_added_during_payment_stay_in_cart(self):
        session = self.start()
        other = Product.objects.create(name='Saw', price=5)
        OrderItem.objects.create(order=self.cart, product=other, quantity=1)
        self.finish(session, self.gateway.add_payment(session['order_id']))
        self.assertEqual(list(self.cart.orderitem_set.values_list('product__name', flat=True)), ['Saw'])

    def test_cart_edit_after_start_cannot_change_amount_due(self):
        session = self.start()
        OrderItem.objects.filter(order=self.cart).update(quantity=50)
        order = Payment.objects.get(gateway_order_id=session['order_id']).order
        self.assertEqual(order.get_cart_total, Decimal('21.00'))

    def test_price_change_does_not_alter_past_order(self):
        session = self.start()
        self.finish(session, self.gateway.add_payment(session['order_id']))
        Product.objects.filter(pk=self.product.pk).update(price=Decimal('999.00'))
        self.assertEqual(self.pending_order().get_cart_total, Decimal('21.00'))

    def test_deleted_product_does_not_break_past_order(self):
        session = self.start()
        self.finish(session, self.gateway.add_payment(session['order_id']))
        order = self.pending_order()
        self.product.delete()
        response = self.client.get(reverse('order_detail', args=[order.pk]))
        self.assertContains(response, 'Item no longer listed')
        self.assertContains(response, '21.00')


class PaymentFailureTests(PaymentTestCase):
    def test_reported_failure_keeps_cart(self):
        session = self.start()
        response = post_json(self.client, reverse('payment_failed'), {
            'razorpay_order_id': session['order_id'], 'description': 'Card declined',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.pending_order().status, Order.Status.FAILED)
        self.assertEqual(self.cart.get_cart_items, 2)

        page = self.client.get(session['order_url'])
        self.assertContains(page, 'Payment failed')
        self.assertContains(page, 'Card declined')

    def test_retry_after_failure_succeeds(self):
        session = self.start()
        post_json(self.client, reverse('payment_failed'), {'razorpay_order_id': session['order_id']})
        response = self.finish(session, self.gateway.add_payment(session['order_id']))
        self.assertEqual(response.json()['status'], 'paid')
        self.assertEqual(self.pending_order().status, Order.Status.PAID)

    def test_late_failure_cannot_undo_paid_order(self):
        session = self.start()
        self.finish(session, self.gateway.add_payment(session['order_id']))
        post_json(self.client, reverse('payment_failed'), {'razorpay_order_id': session['order_id']})
        self.assertEqual(self.pending_order().status, Order.Status.PAID)

    def test_gateway_reported_failure_is_recorded(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'], status='failed', error='Bank declined')
        self.assertEqual(self.finish(session, payment_id).json()['status'], 'failed')
        self.assertEqual(Payment.objects.get().error_message, 'Bank declined')


class WebhookTests(PaymentTestCase):
    def entity(self, session, payment_id):
        return {'id': payment_id, 'order_id': session['order_id']}

    def test_captured_event_settles_order_without_browser(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'])
        self.assertEqual(self.webhook('payment.captured', self.entity(session, payment_id)).status_code, 200)
        self.assertEqual(self.pending_order().status, Order.Status.PAID)
        self.assertEqual(self.cart.get_cart_items, 0)

    def test_order_paid_event_settles_order(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'])
        self.webhook('order.paid', self.entity(session, payment_id))
        self.assertEqual(self.pending_order().status, Order.Status.PAID)

    def test_duplicate_events_are_harmless(self):
        session = self.start()
        OrderItem.objects.filter(order=self.cart).update(quantity=5)
        payment_id = self.gateway.add_payment(session['order_id'])
        for _ in range(3):
            self.assertEqual(self.webhook('payment.captured', self.entity(session, payment_id)).status_code, 200)
        self.assertEqual(self.cart.get_cart_items, 3)

    def test_bad_signature_is_rejected(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'])
        response = self.webhook('payment.captured', self.entity(session, payment_id), secret='wrong')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.pending_order().status, Order.Status.PENDING)

    def test_missing_signature_is_rejected(self):
        response = self.client.post(reverse('payment_webhook'), b'{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    @override_settings(RAZORPAY_WEBHOOK_SECRET='')
    def test_webhook_refused_when_no_secret_configured(self):
        self.assertEqual(self.webhook('payment.captured', {}, secret='').status_code, 400)

    def test_forged_event_details_are_checked_against_api(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'], status='created')
        self.webhook('payment.captured', self.entity(session, payment_id))
        self.assertEqual(self.pending_order().status, Order.Status.PENDING)

    def test_failed_event_records_failure(self):
        session = self.start()
        entity = {**self.entity(session, 'pay_X'), 'error_description': 'Insufficient funds'}
        self.webhook('payment.failed', entity)
        self.assertEqual(Payment.objects.get().error_message, 'Insufficient funds')

    def test_unknown_order_and_event_are_ignored(self):
        self.assertEqual(self.webhook('payment.captured', {'id': 'pay_1', 'order_id': 'order_other'}).json(), {'status': 'ignored'})
        self.assertEqual(self.webhook('refund.created', None).json(), {'status': 'ignored'})

    def test_gateway_outage_asks_razorpay_to_retry(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'])
        self.gateway.down = True
        with self.assertLogs('store.views', level='ERROR'):
            self.assertEqual(self.webhook('payment.captured', self.entity(session, payment_id)).status_code, 503)

    def test_webhook_works_without_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse('payment_webhook'), b'{}', content_type='application/json').status_code, 400)


class OrderPageTests(PaymentTestCase):
    def test_page_recovers_payment_when_callback_was_missed(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'])
        self.assertContains(self.client.get(session['order_url']), 'Payment successful')
        self.assertEqual(self.pending_order().transaction_id, payment_id)

    def test_unpaid_order_shows_pending(self):
        session = self.start()
        self.assertContains(self.client.get(session['order_url']), 'Payment not confirmed yet')

    def test_page_survives_gateway_outage(self):
        session = self.start()
        self.gateway.down = True
        with self.assertLogs('store.views', level='ERROR'):
            self.assertEqual(self.client.get(session['order_url']).status_code, 200)

    def test_other_customers_cannot_see_or_settle_an_order(self):
        session = self.start()
        payment_id = self.gateway.add_payment(session['order_id'])
        other = Client()
        other.force_login(User.objects.create_user('other', 'o@example.com', STRONG_PASSWORD))
        self.assertEqual(other.get(session['order_url']).status_code, 404)
        self.assertEqual(Client().get(session['order_url']).status_code, 404)
        self.assertEqual(self.finish(session, payment_id, client=other).status_code, 404)
        self.assertEqual(post_json(other, reverse('payment_failed'), {'razorpay_order_id': session['order_id']}).status_code, 404)

    def test_cart_orders_have_no_order_page(self):
        self.assertEqual(self.client.get(reverse('order_detail', args=[self.cart.pk])).status_code, 404)

    def test_staff_can_view_orders(self):
        session = self.start()
        staff = Client()
        member = User.objects.create_user('staff', 's@example.com', STRONG_PASSWORD, is_staff=True)
        staff.force_login(member)
        self.assertEqual(staff.get(session['order_url']).status_code, 404)  # staff with no role
        member.groups.add(Group.objects.get(name='Support Staff'))
        self.assertEqual(staff.get(session['order_url']).status_code, 200)
        self.assertEqual(self.finish(session, 'pay_x', client=staff).status_code, 404)  # can view, not act

    def test_legacy_order_without_payment_renders(self):
        order = Order.objects.create(customer=self.user.customer, status=Order.Status.PAID, transaction_id='1234.5')
        self.assertEqual(self.client.get(reverse('order_detail', args=[order.pk])).status_code, 200)


class GuestCheckoutTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.guest = Client()
        self.guest.cookies['cart'] = json.dumps({str(self.product.pk): {'quantity': 3}})
        self.details = {'name': 'Ravi', 'email': 'ravi@example.com'}

    def test_full_guest_checkout(self):
        self.assertContains(self.guest.get(reverse('checkout')), 'name="email"')

        session = self.start(self.guest, form=self.details)
        self.assertEqual(session['amount'], 3150)
        self.assertEqual(session['prefill'], self.details)

        response = self.finish(session, self.gateway.add_payment(session['order_id']), client=self.guest)
        self.assertEqual(response.json()['status'], 'paid')

        page = self.guest.get(response.json()['redirect_url'])
        self.assertContains(page, 'Payment successful')
        self.assertEqual(page.cookies['cart'].value, '')  # guest cart cleared
        self.assertEqual(Customer.objects.get(email='ravi@example.com').name, 'Ravi')

    def test_guest_details_are_required(self):
        for form in ({}, {'name': 'Ravi'}, {'name': 'Ravi', 'email': 'bad'}, {'name': ' ', 'email': 'a@b.co'}):
            response = post_json(self.guest, reverse('payment_create'), {'form': form, 'shipping': SHIPPING})
            self.assertEqual(response.status_code, 400, form)

    def test_guest_cannot_attach_order_to_registered_account(self):
        session = self.start(self.guest, form={'name': 'Mallory', 'email': 'u@example.com'})
        self.user.customer.refresh_from_db()
        self.assertNotEqual(self.user.customer.name, 'Mallory')
        order = Payment.objects.get(gateway_order_id=session['order_id']).order
        self.assertIsNone(order.customer.user)
        self.assertEqual(self.client.get(session['order_url']).status_code, 404)

    def test_returning_guest_reuses_customer_even_with_duplicates(self):
        Customer.objects.create(email='ravi@example.com')
        Customer.objects.create(email='ravi@example.com')
        self.assertEqual(self.start(self.guest, form=self.details)['amount'], 3150)

    def test_guest_cart_kept_until_paid(self):
        session = self.start(self.guest, form=self.details)
        page = self.guest.get(session['order_url'])
        self.assertNotIn('cart', page.cookies)


class MoneyTests(TestCase):
    def test_minor_units_are_exact(self):
        self.assertEqual(payments.to_minor_units(Decimal('19.99')), 1999)
        self.assertEqual(payments.to_minor_units(Decimal('0.29')), 29)
        self.assertEqual(payments.to_minor_units(Decimal('1.15') * 3), 345)

    def test_real_request_wrapper_reports_network_errors(self):
        with mock.patch('store.payments.requests.request', side_effect=requests.ConnectTimeout):
            with self.assertRaises(payments.PaymentError):
                payments._request('GET', '/payments/pay_1')

    def test_real_request_wrapper_reports_http_errors(self):
        response = requests.Response()
        response.status_code = 401
        response._content = b'{"error":"bad key"}'
        with mock.patch('store.payments.requests.request', return_value=response):
            with self.assertRaisesRegex(payments.PaymentError, '401'):
                payments._request('GET', '/payments/pay_1')

    def test_malformed_payment_id_never_reaches_api(self):
        with mock.patch('store.payments._request') as request:
            with self.assertRaises(payments.PaymentError):
                payments.settle(Payment(gateway_order_id='o', amount=1, currency='INR'), '../orders/x')
        request.assert_not_called()


class SeedDataTests(TestCase):
    def test_new_database_has_catalogue(self):
        self.assertEqual(Product.objects.count(), 9)
        camera = Product.objects.get(name='Camera')
        self.assertEqual(camera.price, Decimal('175.00'))
        self.assertEqual(camera.imageURL, '/images/camera.jpg')


class NavigationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('u', 'u@example.com', STRONG_PASSWORD)

    def test_pages_cannot_be_kept_by_the_browser(self):
        for client in (Client(), self.client):
            for name in ('store', 'cart', 'login'):
                self.assertIn('no-store', client.get(reverse(name)).get('Cache-Control', ''), name)
            self.client.force_login(self.user)
        self.assertIn('no-store', self.client.get(reverse('cart_data')).get('Cache-Control', ''))

    def test_static_images_stay_cacheable(self):
        response = self.client.get('/images/camera.jpg')
        self.assertNotIn('no-store', response.get('Cache-Control', ''))
        b''.join(response.streaming_content)

    def test_logged_in_users_are_sent_away_from_sign_in_pages(self):
        self.client.force_login(self.user)
        for name in ('login', 'signup', 'forgot_password'):
            self.assertRedirects(self.client.get(reverse(name)), reverse('store'), msg_prefix=name)

    def test_logged_in_user_with_next_continues_there(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse('login') + '?next=/cart/'), '/cart/')
        self.assertRedirects(self.client.get(reverse('login') + '?next=https://evil.example.com/'), reverse('store'))

    def test_logout_ends_access_to_own_orders(self):
        self.client.force_login(self.user)
        order = Order.objects.create(customer=self.user.customer, status=Order.Status.PAID)
        url = reverse('order_detail', args=[order.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(reverse('logout'))
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_failed_forms_redirect_so_back_never_resubmits(self):
        bad_login = self.client.post(reverse('login'), {'username': 'u', 'pass': 'wrong', 'next': '/cart/'})
        self.assertRedirects(bad_login, reverse('login') + '?next=%2Fcart%2F', fetch_redirect_response=False)
        bad_signup = self.client.post(reverse('signup'), {'username': 'kept', 'email': 'bad', 'password1': 'x', 'password2': 'x'})
        self.assertRedirects(bad_signup, reverse('signup'), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse('signup')), 'value="kept"')
        self.assertNotContains(self.client.get(reverse('signup')), 'value="kept"')

    def test_guest_checkout_page_is_gone_after_cart_is_empty(self):
        self.assertRedirects(self.client.get(reverse('checkout')), reverse('cart'))


class LoginLockoutTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('u', 'u@example.com', STRONG_PASSWORD)

    def attempt(self, password='wrong', client=None, **extra):
        return (client or self.client).post(
            reverse('login'), {'username': 'u', 'pass': password}, follow=True, **extra,
        )

    def test_correct_password_is_refused_during_lockout(self):
        for _ in range(5):
            self.assertContains(self.attempt(), 'incorrect')
        response = self.attempt(STRONG_PASSWORD)
        self.assertContains(response, 'Too many failed attempts')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_success_clears_the_count(self):
        for _ in range(4):
            self.attempt()
        self.assertIn('_auth_user_id', self.attempt(STRONG_PASSWORD) and self.client.session)
        self.client.logout()
        for _ in range(4):
            self.assertContains(self.attempt(), 'incorrect')

    def test_lockout_is_per_address(self):
        for _ in range(5):
            self.attempt(HTTP_X_FORWARDED_FOR='1.1.1.1')
        other = Client()
        self.attempt(STRONG_PASSWORD, client=other, HTTP_X_FORWARDED_FOR='2.2.2.2')
        self.assertIn('_auth_user_id', other.session)

    def test_spoofed_forwarded_entries_do_not_evade_lockout(self):
        for fake in range(5):
            self.attempt(HTTP_X_FORWARDED_FOR=f'9.9.9.{fake}, 1.1.1.1')
        self.assertContains(self.attempt(STRONG_PASSWORD, HTTP_X_FORWARDED_FOR='8.8.8.8, 1.1.1.1'), 'Too many failed attempts')


class AdminAccessTests(TestCase):
    PROTECTED = ['/admin/', '/admin/auth/user/', '/admin/auth/group/', '/admin/store/order/',
                 '/admin/store/payment/', '/admin/store/product/', '/admin/store/customer/']

    def setUp(self):
        cache.clear()
        self.superuser = User.objects.create_superuser('root', 'r@example.com', STRONG_PASSWORD)
        self.member = User.objects.create_user('member', 'm@example.com', STRONG_PASSWORD)
        self.product = Product.objects.first()

    def staff(self, role=None, username='staffer'):
        user = User.objects.create_user(username, f'{username}@example.com', STRONG_PASSWORD, is_staff=True)
        if role:
            user.groups.add(Group.objects.get(name=role))
        client = Client()
        client.force_login(user)
        return client

    def test_anonymous_visitors_are_sent_to_the_site_login(self):
        for path in self.PROTECTED:
            response = self.client.get(path, follow=True)
            self.assertEqual(response.redirect_chain[-1][0].split('?')[0], reverse('login'), path)
            self.assertContains(response, 'Login Here')

    def test_customers_get_access_denied_everywhere(self):
        self.client.force_login(self.member)
        for path in self.PROTECTED:
            self.assertContains(self.client.get(path), 'Access Denied', status_code=403, msg_prefix=path)

    def test_customers_cannot_post_to_admin(self):
        self.client.force_login(self.member)
        response = self.client.post('/admin/store/product/add/', {'name': 'Hack', 'price': '1'})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Product.objects.filter(name='Hack').exists())

    def test_admin_login_form_is_not_exposed(self):
        response = self.client.get('/admin/login/?next=/admin/store/order/')
        self.assertRedirects(response, '/login/?next=%2Fadmin%2Fstore%2Forder%2F')
        self.client.post('/admin/login/', {'username': 'root', 'password': STRONG_PASSWORD})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_logs_in_through_the_site_and_returns_to_admin(self):
        response = self.client.post(
            reverse('login'), {'username': 'root', 'pass': STRONG_PASSWORD, 'next': '/admin/'}, follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Rento Administration')

    def test_staff_sessions_are_short(self):
        self.client.post(reverse('login'), {'username': 'root', 'pass': STRONG_PASSWORD})
        self.assertEqual(self.client.session.get_expiry_age(), 3600)
        customer = Client()
        customer.post(reverse('login'), {'username': 'member', 'pass': STRONG_PASSWORD})
        self.assertGreater(customer.session.get_expiry_age(), 3600)

    def test_superuser_reaches_everything(self):
        self.client.force_login(self.superuser)
        for path in self.PROTECTED:
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_staff_without_a_role_sees_nothing(self):
        client = self.staff()
        self.assertEqual(client.get('/admin/').status_code, 200)
        for path in self.PROTECTED[1:]:
            self.assertEqual(client.get(path).status_code, 403, path)

    def test_support_staff_is_read_only(self):
        client = self.staff('Support Staff')
        for path in ('/admin/store/order/', '/admin/store/payment/', '/admin/store/customer/', '/admin/store/product/'):
            self.assertEqual(client.get(path).status_code, 200, path)
        self.assertEqual(client.get('/admin/store/product/add/').status_code, 403)
        change = f'/admin/store/product/{self.product.pk}/change/'
        self.assertEqual(client.post(change, {'name': 'Hacked', 'price': '1'}).status_code, 403)
        self.product.refresh_from_db()
        self.assertNotEqual(self.product.name, 'Hacked')

    def test_store_manager_runs_products_only(self):
        client = self.staff('Store Manager')
        self.assertEqual(client.get('/admin/store/product/add/').status_code, 200)
        order = Order.objects.create(customer=self.member.customer, status=Order.Status.PENDING)
        response = client.post(f'/admin/store/order/{order.pk}/change/', {'status': 'paid'})
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)

    def test_only_superusers_manage_accounts_and_roles(self):
        for role, name in (('Store Manager', 'a'), ('Support Staff', 'b')):
            client = self.staff(role, username=name)
            for path in ('/admin/auth/user/', '/admin/auth/group/', f'/admin/auth/user/{self.member.pk}/change/'):
                self.assertEqual(client.get(path).status_code, 403, path)
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_staff)

    def test_payment_records_cannot_be_deleted_or_rechecked_by_staff(self):
        order = Order.objects.create(customer=self.member.customer, status=Order.Status.PENDING)
        payment = Payment.objects.create(order=order, gateway_order_id='order_x', amount=100, currency='INR')
        client = self.staff('Store Manager')
        self.assertEqual(client.post(f'/admin/store/payment/{payment.pk}/delete/').status_code, 403)
        page = client.get('/admin/store/payment/')
        self.assertNotContains(page, 'recheck_with_gateway')
        self.client.force_login(self.superuser)
        self.assertContains(self.client.get('/admin/store/payment/'), 'recheck_with_gateway')
        self.assertEqual(self.client.post(f'/admin/store/payment/{payment.pk}/delete/').status_code, 403)

    def test_roles_grant_only_store_permissions(self):
        for name in ('Store Manager', 'Support Staff'):
            labels = set(Group.objects.get(name=name).permissions.values_list('content_type__app_label', flat=True))
            self.assertEqual(labels, {'store'}, name)

    def test_admin_link_only_shown_to_staff(self):
        self.client.force_login(self.member)
        self.assertNotContains(self.client.get(reverse('store')), 'href="/admin/"')
        self.assertContains(self.staff().get(reverse('store')), 'href="/admin/"')

    def test_admin_pages_are_not_kept_by_the_browser(self):
        self.client.force_login(self.superuser)
        self.assertIn('no-store', self.client.get('/admin/').get('Cache-Control', ''))


class EmailLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('asha', 'Asha@Example.com', STRONG_PASSWORD)

    def login(self, identifier, password=STRONG_PASSWORD):
        client = Client()
        client.post(reverse('login'), {'username': identifier, 'pass': password})
        return client.session.get('_auth_user_id')

    def test_username_and_email_both_work(self):
        self.assertEqual(self.login('asha'), str(self.user.pk))
        self.assertEqual(self.login('Asha@Example.com'), str(self.user.pk))
        self.assertEqual(self.login('asha@example.com'), str(self.user.pk))

    def test_wrong_password_fails_either_way(self):
        self.assertIsNone(self.login('asha', 'wrong-password'))
        self.assertIsNone(self.login('asha@example.com', 'wrong-password'))
        self.assertIsNone(self.login('nobody@example.com'))

    def test_inactive_account_cannot_log_in_by_email(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertIsNone(self.login('asha@example.com'))

    def test_shared_email_logs_into_the_account_whose_password_matches(self):
        other = User.objects.create_user('acg', 'asha@example.com', 'another-strong-pass-7')
        self.assertEqual(self.login('asha@example.com'), str(self.user.pk))
        self.assertEqual(self.login('asha@example.com', 'another-strong-pass-7'), str(other.pk))

    def test_username_that_looks_like_an_email_wins_over_email_match(self):
        odd = User.objects.create_user('asha@example.com', 'odd@example.com', 'another-strong-pass-7')
        self.assertEqual(self.login('asha@example.com', 'another-strong-pass-7'), str(odd.pk))
        self.assertIsNone(self.login('asha@example.com'))

    def test_login_form_says_either_is_accepted(self):
        self.assertContains(self.client.get(reverse('login')), 'Username or Email')


class FakePayPal:
    """Stands in for paypal._request, behaving like PayPal's Orders API."""

    def __init__(self):
        self.orders = {}
        self.calls = []
        self.down = False
        self.next_capture_status = 'COMPLETED'

    def __call__(self, method, path, payload=None, request_id=None):
        self.calls.append((method, path))
        if self.down:
            raise payments.PaymentError('Could not reach PayPal: ConnectionError')
        if method == 'POST' and path == '/v2/checkout/orders':
            order_id = f'PP{len(self.orders) + 1}'
            self.orders[order_id] = {'id': order_id, 'status': 'CREATED', 'purchase_units': payload['purchase_units']}
            return self.orders[order_id]
        order_id = path.split('/')[4]
        order = self.orders[order_id]
        if path.endswith('/capture'):
            if order['status'] == 'COMPLETED':
                raise payments.PaymentError('PayPal returned 422: ORDER_ALREADY_CAPTURED')
            if self.next_capture_status != 'COMPLETED':
                raise payments.PaymentError('PayPal returned 422: INSTRUMENT_DECLINED')
            order['status'] = 'COMPLETED'
            unit = order['purchase_units'][0]
            unit['payments'] = {'captures': [{'id': f'CAP{order_id}', 'status': 'COMPLETED', 'amount': dict(unit['amount'])}]}
        return order


@override_settings(
    PAYMENT_GATEWAY='paypal', PAYMENT_CURRENCY='USD',
    PAYPAL_CLIENT_ID='client-id', PAYPAL_CLIENT_SECRET='',
)
class PayPalTestCase(TestCase):
    def setUp(self):
        self.gateway = FakePayPal()
        patcher = mock.patch('store.paypal._request', self.gateway)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.product = Product.objects.create(name='Drill', price=Decimal('10.50'))
        self.user = User.objects.create_user('u', 'u@example.com', STRONG_PASSWORD)
        self.client.force_login(self.user)
        self.cart = Order.objects.create(customer=self.user.customer)
        OrderItem.objects.create(order=self.cart, product=self.product, quantity=2)

    def start(self):
        response = post_json(self.client, reverse('payment_create'), {'shipping': SHIPPING})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def verify(self, session, **extra):
        return post_json(self.client, reverse('payment_verify'), {'order_id': session['order_id'], **extra})

    def order(self):
        return Order.objects.exclude(status=Order.Status.CART).latest('pk')


class PayPalWithoutSecretTests(PayPalTestCase):
    def test_checkout_works_with_only_a_client_id(self):
        page = self.client.get(reverse('checkout'))
        self.assertContains(page, 'paypal.com/sdk/js?client-id=client-id&currency=USD')
        self.assertContains(page, '$21.00')
        self.assertNotContains(page, 'razorpay.com')
        session = self.start()
        self.assertEqual(session['value'], '21.00')
        self.assertEqual(session['currency'], 'USD')
        self.assertEqual(self.gateway.calls, [])

    def test_browser_report_never_marks_an_order_paid(self):
        session = self.start()
        response = self.verify(session, capture_id='CAPTURE123')
        self.assertEqual(response.json()['status'], 'review')
        order = self.order()
        self.assertEqual(order.status, Order.Status.REVIEW)
        self.assertFalse(order.complete)
        self.assertEqual(order.transaction_id, 'CAPTURE123')
        self.assertContains(self.client.get(session['order_url']), 'Payment received')
        self.assertEqual(self.cart.get_cart_items, 0)

    def test_report_without_a_capture_id_changes_nothing(self):
        session = self.start()
        for bad in ('', '../x', 'a b c d e'):
            with self.assertLogs('store.views', level='ERROR'):
                self.assertEqual(self.verify(session, capture_id=bad).status_code, 202)
        self.assertEqual(self.order().status, Order.Status.PENDING)
        self.assertEqual(self.cart.get_cart_items, 2)

    def test_repeat_reports_only_reduce_the_cart_once(self):
        OrderItem.objects.filter(order=self.cart).update(quantity=2)
        session = self.start()
        OrderItem.objects.filter(order=self.cart).update(quantity=5)
        self.verify(session, capture_id='CAPTURE123')
        self.verify(session, capture_id='CAPTURE999')
        self.assertEqual(self.cart.get_cart_items, 3)
        self.assertEqual(self.order().transaction_id, 'CAPTURE123')

    def test_other_customers_cannot_report_a_payment(self):
        session = self.start()
        other = Client()
        other.force_login(User.objects.create_user('other', 'o@example.com', STRONG_PASSWORD))
        response = post_json(other, reverse('payment_verify'), {'order_id': session['order_id'], 'capture_id': 'CAPTURE123'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.order().status, Order.Status.PENDING)

    def test_late_failure_cannot_undo_a_reported_payment(self):
        session = self.start()
        self.verify(session, capture_id='CAPTURE123')
        post_json(self.client, reverse('payment_failed'), {'order_id': session['order_id']})
        self.assertEqual(self.order().status, Order.Status.REVIEW)

    def admin_action(self, client, action, payment):
        return client.post('/admin/store/payment/', {
            'action': action, '_selected_action': [payment.pk],
        })

    def test_superuser_confirms_or_rejects_in_admin(self):
        session = self.start()
        self.verify(session, capture_id='CAPTURE123')
        payment = Payment.objects.get(gateway_order_id=session['order_id'])
        OrderItem.objects.create(order=self.cart, product=self.product, quantity=1)
        boss = Client()
        boss.force_login(User.objects.create_superuser('root', 'r@example.com', STRONG_PASSWORD))
        self.admin_action(boss, 'confirm_reviewed', payment)
        self.assertEqual(self.order().status, Order.Status.PAID)
        self.assertEqual(self.cart.get_cart_items, 1)  # not reduced a second time

    def test_rejecting_marks_the_order_failed(self):
        session = self.start()
        self.verify(session, capture_id='CAPTURE123')
        payment = Payment.objects.get(gateway_order_id=session['order_id'])
        boss = Client()
        boss.force_login(User.objects.create_superuser('root', 'r@example.com', STRONG_PASSWORD))
        self.admin_action(boss, 'reject_reviewed', payment)
        self.assertEqual(self.order().status, Order.Status.FAILED)

    def test_store_manager_cannot_confirm_payments(self):
        session = self.start()
        self.verify(session, capture_id='CAPTURE123')
        payment = Payment.objects.get(gateway_order_id=session['order_id'])
        manager = User.objects.create_user('m', 'm@example.com', STRONG_PASSWORD, is_staff=True)
        manager.groups.add(Group.objects.get(name='Store Manager'))
        client = Client()
        client.force_login(manager)
        self.admin_action(client, 'confirm_reviewed', payment)
        self.assertEqual(self.order().status, Order.Status.REVIEW)


@override_settings(PAYPAL_CLIENT_SECRET='secret')
class PayPalVerifiedTests(PayPalTestCase):
    def test_paid_only_after_server_capture(self):
        session = self.start()
        self.assertEqual(session['order_id'], 'PP1')
        self.assertEqual(self.gateway.orders['PP1']['purchase_units'][0]['amount'], {'currency_code': 'USD', 'value': '21.00'})
        self.assertEqual(self.order().status, Order.Status.PENDING)

        response = self.verify(session)
        self.assertEqual(response.json()['status'], 'paid')
        self.assertTrue(self.order().complete)
        self.assertEqual(self.order().transaction_id, 'CAPPP1')
        self.assertEqual(self.cart.get_cart_items, 0)

    def test_browser_cannot_supply_its_own_capture(self):
        session = self.start()
        self.gateway.next_capture_status = 'DECLINED'
        response = self.verify(session, capture_id='FAKECAPTURE')
        self.assertNotEqual(response.json().get('status'), 'paid')
        self.assertFalse(self.order().complete)
        self.assertEqual(self.cart.get_cart_items, 2)

    def test_wrong_amount_is_rejected(self):
        session = self.start()
        self.gateway.orders['PP1']['purchase_units'][0]['amount']['value'] = '0.01'
        with self.assertLogs('store.paypal', level='ERROR'):
            self.assertEqual(self.verify(session).json()['status'], 'failed')
        self.assertFalse(self.order().complete)

    def test_wrong_currency_is_rejected(self):
        session = self.start()
        self.gateway.orders['PP1']['purchase_units'][0]['amount']['currency_code'] = 'EUR'
        with self.assertLogs('store.paypal', level='ERROR'):
            self.assertEqual(self.verify(session).json()['status'], 'failed')

    def test_already_captured_order_still_settles(self):
        session = self.start()
        self.verify(session)
        Payment.objects.update(status=Payment.Status.CREATED)
        Order.objects.filter(pk=self.order().pk).update(status=Order.Status.PENDING)
        self.assertEqual(self.verify(session).json()['status'], 'paid')

    def test_outage_keeps_order_pending_and_order_page_recovers(self):
        session = self.start()
        self.gateway.down = True
        with self.assertLogs('store.views', level='ERROR'):
            self.assertEqual(self.verify(session).status_code, 202)
        self.assertEqual(self.order().status, Order.Status.PENDING)

        self.gateway.down = False
        unit = self.gateway.orders['PP1']
        unit['status'] = 'COMPLETED'
        unit['purchase_units'][0]['payments'] = {'captures': [{'id': 'CAPX', 'status': 'COMPLETED', 'amount': dict(unit['purchase_units'][0]['amount'])}]}
        self.assertContains(self.client.get(session['order_url']), 'Payment successful')

    def test_outage_when_starting_reports_cleanly(self):
        self.gateway.down = True
        with self.assertLogs('store.views', level='ERROR'):
            response = post_json(self.client, reverse('payment_create'), {'shipping': SHIPPING})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(self.cart.get_cart_items, 2)

    def test_real_wrapper_uses_chosen_environment(self):
        with override_settings(PAYPAL_MODE='live'):
            self.assertEqual(paypal._api_base(), 'https://api-m.paypal.com')
        with override_settings(PAYPAL_MODE='sandbox'):
            self.assertEqual(paypal._api_base(), 'https://api-m.sandbox.paypal.com')
