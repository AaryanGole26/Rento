from django.shortcuts import render
from django.urls import reverse
from django.utils.cache import add_never_cache_headers

UNCACHED_TYPES = ('text/html', 'application/json')


class NoStoreMiddleware:
    """Stops browsers keeping pages, so Back and Forward always ask the server again.

    Every page depends on who is logged in, even if only the navigation bar. Without
    this, Back after logging out can redisplay a member's cart or order. Static files
    and images are unaffected and stay cacheable.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.get('Content-Type', '').startswith(UNCACHED_TYPES):
            add_never_cache_headers(response)
        return response


class AdminAccessMiddleware:
    """Refuses logged-in non-staff accounts anywhere under the admin site.

    Anonymous visitors carry on to the admin's own check, which sends them to log in.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (request.user.is_authenticated
                and not request.user.is_staff
                and request.path.startswith(reverse('admin:index'))):
            return render(request, '403.html', status=403)
        return self.get_response(request)
