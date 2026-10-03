from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

STAFF_ROLES = ("owner", "front_desk", "bar_staff", "shop_staff")


def role_required(*roles):
    """Allow only the listed roles (the Django superuser always passes).

    The check runs on the server for every request, so hiding a button in the
    template is never the only protection.
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if request.user.is_superuser or request.user.role in roles:
                return view(request, *args, **kwargs)
            raise PermissionDenied

        return wrapper

    return decorator
