from django.conf import settings

CURRENCY_SYMBOLS = {'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£'}


def currency(request):
    code = settings.PAYMENT_CURRENCY
    return {'currency_symbol': CURRENCY_SYMBOLS.get(code, f'{code} ')}
