from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class UsernameOrEmailBackend(ModelBackend):
    """Logs in with either a username or an email address.

    An exact username match wins. Older accounts can share an email address, so each
    one is tried and the password decides which account is logged in.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None

        User = get_user_model()
        candidates = list(User.objects.filter(username=username))
        if not candidates and '@' in username:
            candidates = list(User.objects.filter(email__iexact=username).order_by('pk'))

        for user in candidates:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user

        if not candidates:
            User().set_password(password)  # same work as a real check, so timing reveals nothing
        return None
