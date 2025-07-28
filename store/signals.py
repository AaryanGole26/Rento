from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User

@receiver(post_save, sender=User)
def create_customer(sender, instance, created, **kwargs):
    from .models import Customer
    if created and not hasattr(instance, 'customer'):
        Customer.objects.create(user=instance)

