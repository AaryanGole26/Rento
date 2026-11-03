from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Customer


@receiver(post_save, sender=User)
def create_customer(sender, instance, created, raw=False, **kwargs):
    if created and not raw:
        Customer.objects.get_or_create(user=instance, defaults={'email': instance.email})
