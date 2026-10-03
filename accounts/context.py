from django.conf import settings

from .permissions import STAFF_ROLES

STAFF_PREFIXES = ("/desk/", "/bar/", "/owner/", "/staff/")


def ui(request):
    """Layout helpers for templates: which shell to draw, and which sidebar sections this person may see.

    These only decide what to *show*. Every page still enforces its own permission on the server."""
    user = request.user
    if not user.is_authenticated:
        return {"club": settings.CLUB}
    superuser = user.is_superuser
    role = user.role
    is_staff = superuser or role in STAFF_ROLES
    return {
        "club": settings.CLUB,
        "is_staff_user": is_staff,
        # Staff pages get the sidebar shell; everyone else (and the public site) gets the top-bar shell.
        "staff_shell": is_staff and request.path.startswith(STAFF_PREFIXES),
        "can_courts": superuser or role in ("owner", "front_desk"),
        "can_shop": superuser or role in ("owner", "front_desk", "shop_staff"),
        "can_bar": superuser or role in ("owner", "bar_staff"),
        "can_owner": superuser or role == "owner",
    }
