from django.apps import AppConfig
from django.db.models.signals import post_migrate


class StoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'store'

    def ready(self):
        from . import signals  # noqa: F401
        from .roles import sync_roles

        post_migrate.connect(sync_roles, sender=self)
