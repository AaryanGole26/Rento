# Rento

Rento is an online store for renting engineering tools and equipment, from toolkits and soldering irons to laptops and cameras. Customers can browse the catalogue, fill a cart with or without an account, and pay online through Razorpay.

Built with Django and designed to deploy on [Render](https://render.com).

## Features

- **Catalogue and cart:** browse equipment and manage a cart. Guest carts are kept in a cookie and move into the account on login.
- **Guest or account checkout:** customers can buy with just a name and email, or sign in first.
- **Razorpay payments:** cards, UPI, netbanking and wallets, with every payment verified on the server. PayPal is available as an alternative.
- **Order status page:** shows whether a payment succeeded, failed or is still pending, along with the transaction ID.
- **Accounts:** sign up, log in with a username or email, and reset a forgotten password with a single-use emailed link that expires after one hour.
- **Admin panel:** manage products, orders and customers at `/admin/`, and re-check any payment with Razorpay.
- **Interface:** responsive glass design with dark and light themes. It uses no front-end frameworks and self-hosts its font, so pages stay small.
- **Access control:** role-based admin, failed-login lockout, and pages that can't be reopened from browser history after logging out.

## Tech stack

| Area | Technology |
| --- | --- |
| Backend | Python 3.12, Django 5.2 |
| Database | PostgreSQL in production, SQLite locally |
| Frontend | Django templates, CSS, vanilla JavaScript |
| Payments | Razorpay Orders API and Checkout, or PayPal |
| Email | Brevo HTTP API or any SMTP server |
| Hosting | Render, Gunicorn, WhiteNoise |

## Run it locally

You need Python 3.12.

```bash
git clone https://github.com/AaryanGole26/Rento.git
cd Rento

python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate

pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/>. Migrating a new database also adds the starter catalogue.

The defaults in `.env.example` run the whole site except payments. Without email settings, password reset emails are printed in the terminal. To enable payments, add your Razorpay test keys as described in [Payments](#payments). Restart the server after changing `.env`, because it's only read at startup.

## Configuration

Settings come from environment variables: a `.env` file locally and the **Environment** tab on Render. The `.env` file is ignored by git and should never be committed.

| Variable | Required | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | In production | Django's signing key. The app won't start without it when `DEBUG` is off. |
| `DEBUG` | No | `True` for local development. Defaults to off. |
| `ALLOWED_HOSTS` | No | Extra hostnames, comma separated. Render's hostname is added automatically. |
| `CSRF_TRUSTED_ORIGINS` | No | Needed only for custom domains, for example `https://rento.example.com`. |
| `BEHIND_PROXY` | No | Set to `True` behind an HTTPS proxy other than Render. |
| `DATABASE_URL` | In production | PostgreSQL connection URL. Falls back to local SQLite when empty. |
| `BREVO_API_KEY` | For email on Render | Sends email through Brevo's HTTP API. |
| `DEFAULT_FROM_EMAIL` | For email | Sender address, for example `Rento <you@example.com>`. Must be a verified sender in Brevo. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | No | SMTP alternative, used when `BREVO_API_KEY` is empty. |
| `PAYMENT_GATEWAY` | No | `razorpay` (default) or `paypal`. |
| `PAYMENT_CURRENCY` | No | Three-letter code with two decimal places. Defaults to `INR` for Razorpay and `USD` for PayPal, which doesn't accept `INR`. |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | For payments | API keys from the Razorpay dashboard. |
| `RAZORPAY_WEBHOOK_SECRET` | In production | A long random string you choose, also entered when creating the webhook. |
| `PAYPAL_CLIENT_ID` | No | Defaults to the project's existing ID. |
| `PAYPAL_CLIENT_SECRET` | No | With PayPal, turns on automatic payment verification. |
| `PAYPAL_MODE` | With the secret | `sandbox` (default) or `live`, matching the PayPal app. |
| `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD` | No | Creates the admin account during the build if it doesn't exist. |

## Payments

### Razorpay (default)

1. Create a [Razorpay](https://razorpay.com) account and switch the dashboard to **Test Mode**.
2. Under **Account & Settings → API Keys**, generate a key and set `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`.
3. Under **Account & Settings → Webhooks**, add a webhook:
   - **URL:** `https://<your-site>/payment/webhook/`
   - **Secret:** any long random string, also set as `RAZORPAY_WEBHOOK_SECRET`
   - **Events:** `payment.authorized`, `payment.captured`, `payment.failed`, `order.paid`
4. Pay with Razorpay's [test card details](https://razorpay.com/docs/payments/payments/test-card-details/). No real money moves in Test Mode.

The webhook needs a public URL, so add it after deploying. Local payments still work without it, because the checkout and order pages confirm payments with Razorpay directly.

Moving to real payments means completing Razorpay's account activation and replacing the test keys and webhook with live ones.

### How a Razorpay payment works

1. **Start:** when the customer clicks Pay, the server copies the cart into a pending order, using prices from the database, and creates a matching Razorpay order. The browser never supplies the amount.
2. **Pay:** Razorpay Checkout collects the payment.
3. **Verify:** the server checks Razorpay's signature, then fetches the payment from Razorpay and confirms the order, amount, currency and status. Authorized payments are captured at this point.
4. **Finish:** the order is marked paid, the purchased items leave the cart, and the customer sees the order page.

If a payment fails or is cancelled, the cart is left as it was so the customer can try again.

If the customer closes the page right after paying, the order still completes. The webhook delivers the result independently, and the order page and admin panel can also ask Razorpay directly. These paths all share the same logic, so running them more than once is safe.

### PayPal (optional)

Set `PAYMENT_GATEWAY=paypal` and `PAYMENT_CURRENCY=USD`.

PayPal works straight away using the client ID alone, in one of two modes:

| | Client ID only | Client ID and secret |
| --- | --- | --- |
| Customer pays through | PayPal buttons | PayPal buttons |
| Order status after paying | **Confirming payment** | **Paid** |
| Who confirms the payment | An administrator, in the admin panel | The server, directly with PayPal |

To turn on automatic verification, open your app at [developer.paypal.com](https://developer.paypal.com), copy its secret into `PAYPAL_CLIENT_SECRET`, and set `PAYPAL_MODE` to match the app.

Without the secret, the server can't check a payment with PayPal, so don't ship an order until you've confirmed it. Find the transaction ID in your PayPal account, then in the admin panel go to **Payments**, select the payment, and choose the confirm or reject action. Only superusers can do this.

## Deploy on Render

1. Create a **PostgreSQL** database, using Render or any other provider, and copy its connection URL. Without one, Render's filesystem is wiped on every deploy, taking accounts and orders with it.
2. Create a **Web Service** from this repository:
   - **Build command:** `./build.sh`
   - **Start command:** `gunicorn ecommerce.wsgi:application`
3. Under **Environment**, set `SECRET_KEY`, `DATABASE_URL`, the email variables and the Razorpay variables from [Configuration](#configuration). Leave `DEBUG` unset.
4. Deploy, then add the Razorpay webhook using your new URL.

Each build installs dependencies, collects static files and applies migrations.

### Sending email from Render

Render's free tier blocks outbound SMTP, so Gmail and other SMTP servers will time out there. Use Brevo instead:

1. Create a free [Brevo](https://www.brevo.com) account and verify a sender address.
2. Create an API key under **SMTP & API**.
3. Set `BREVO_API_KEY` and `DEFAULT_FROM_EMAIL`.

To check email delivery, run this from the Render shell:

```bash
python manage.py sendtestemail you@example.com
```

Images uploaded through the admin panel are stored on the server's disk, so on Render they are lost at the next deploy unless you attach a persistent disk. Images committed to `static/images/` are not affected.

## Admin access

The admin panel is at `/admin/` and shares the site's login page. Customers get an Access Denied page, and visitors who aren't logged in are sent to log in.

| Role | How to assign it | Can do |
| --- | --- | --- |
| Superuser | `python manage.py createsuperuser` | Everything, including accounts, roles and re-checking payments |
| Store Manager | Staff status + `Store Manager` group | Manage products, view orders, customers and payments |
| Support Staff | Staff status + `Support Staff` group | View products, orders, customers and payments |

A superuser assigns roles on the user's page in the admin panel. Staff with no group can log in but see nothing. The groups are defined in `store/roles.py` and reset to match it on every migrate, so change roles there rather than in the admin panel.

Staff are logged out after an hour of inactivity. After 5 failed logins for the same username and address, logging in is blocked for 15 minutes. Payment records can't be deleted by anyone.

## Tests

```bash
python manage.py test
```

The suite covers accounts, password reset, admin roles, carts, both payment gateways and webhooks. It doesn't contact PayPal or Razorpay or send real email.

## Project structure

```text
ecommerce/          Project settings and root URLs
store/
  models.py         Customer, Product, Order, OrderItem, ShippingAddress, Payment
  views.py          Pages, accounts, cart and payment endpoints
  payments.py       Razorpay client and payment verification
  paypal.py         PayPal client, used with PAYMENT_GATEWAY=paypal
  backends.py       Login with a username or email
  middleware.py     Browser history protection and admin access checks
  roles.py          Staff roles and their permissions
  email_backends.py Brevo email backend
  utils.py          Cart helpers
  templates/        HTML, email and error page templates
  tests.py          Test suite
static/             CSS, JavaScript, fonts and images
build.sh            Render build script
```

## Troubleshooting

| Problem | What to check |
| --- | --- |
| The app won't start: `Set the SECRET_KEY environment variable` | Set `SECRET_KEY`, or `DEBUG=True` when running locally. |
| `400 Bad Request` on every page | The hostname is missing from `ALLOWED_HOSTS`. |
| `403 CSRF verification failed` on a custom domain | Add the domain to `CSRF_TRUSTED_ORIGINS`, including `https://`. |
| Reset emails don't arrive | Run `sendtestemail`, then check the logs and spam folder. Accounts with no email address on file can't receive reset links. |
| Checkout says payments are unavailable | `RAZORPAY_KEY_ID` or `RAZORPAY_KEY_SECRET` isn't set, or the server hasn't been restarted since `.env` changed. |
| PayPal buttons don't appear | Check that the client ID is valid and `PAYMENT_CURRENCY` is one PayPal supports. |
| An order stays pending after payment | Open the order page, or use **Re-check** in the admin panel, then check the webhook's URL and secret. |
