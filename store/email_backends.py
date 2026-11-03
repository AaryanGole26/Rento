"""Sends email through Brevo's HTTP API, for hosts that block outbound SMTP."""

from email.utils import parseaddr

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.utils.html import escape, linebreaks

API_URL = 'https://api.brevo.com/v3/smtp/email'


def _recipients(addresses):
    return [{'email': parseaddr(address)[1]} for address in addresses]


class BrevoEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            try:
                self._send(message)
            except requests.RequestException:
                if not self.fail_silently:
                    raise
            else:
                sent += 1
        return sent

    def _send(self, message):
        sender_name, sender_email = parseaddr(message.from_email)
        html = next(
            (content for content, mimetype in getattr(message, 'alternatives', [])
             if mimetype == 'text/html'),
            linebreaks(escape(message.body)),
        )
        payload = {
            'sender': {'email': sender_email, 'name': sender_name or 'Rento'},
            'to': _recipients(message.to),
            'subject': message.subject,
            'textContent': message.body,
            'htmlContent': html,
        }
        if message.cc:
            payload['cc'] = _recipients(message.cc)
        if message.bcc:
            payload['bcc'] = _recipients(message.bcc)

        response = requests.post(
            API_URL,
            json=payload,
            headers={'api-key': settings.BREVO_API_KEY, 'accept': 'application/json'},
            timeout=settings.EMAIL_TIMEOUT,
        )
        response.raise_for_status()
