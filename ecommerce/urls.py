from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.decorators.cache import cache_control
from django.views.static import serve

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('store.urls')),
    # Product images live outside STATIC_URL, so they need a route of their own
    # (django.conf.urls.static.static() does nothing once DEBUG is off).
    re_path(
        r'^images/(?P<path>.*)$',
        cache_control(public=True, max_age=86400)(serve),
        {'document_root': settings.MEDIA_ROOT},
    ),
]
