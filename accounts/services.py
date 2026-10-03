"""Google/Firebase login logic. Kept out of views so it can be unit-tested with a fake token."""

import json

import firebase_admin
from django.conf import settings
from firebase_admin import auth, credentials
from firebase_admin.exceptions import FirebaseError

from members.models import Member

from .models import User


class InvalidToken(Exception):
    """The browser sent a token we cannot trust."""


def _init_firebase():
    # firebase_admin may only be initialised once per process.
    if not firebase_admin._apps:
        if settings.FIREBASE_CREDENTIALS_JSON:  # deployed: key JSON in an environment variable
            key = credentials.Certificate(json.loads(settings.FIREBASE_CREDENTIALS_JSON))
        else:  # local: key file on disk
            key = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        firebase_admin.initialize_app(key)


def verify_google_token(id_token):
    """Ask Firebase whether the token is genuine; return its claims (email, name...)."""
    _init_firebase()
    try:
        claims = auth.verify_id_token(id_token)
    except (ValueError, FirebaseError) as exc:  # bad, expired or revoked token
        raise InvalidToken(str(exc)) from exc
    if not claims.get("email") or not claims.get("email_verified"):
        raise InvalidToken("Google account has no verified email.")
    return claims


def get_or_create_user(claims):
    """Match by email. New people become plain members; the owner promotes staff in admin."""
    email = claims["email"].lower()
    name = claims.get("name", "")
    user, created = User.objects.get_or_create(
        email=email,
        defaults={"username": email, "first_name": name.split(" ")[0], "role": User.Role.MEMBER},
    )
    if created:
        user.set_unusable_password()  # they only ever sign in through Google
        user.save(update_fields=["password"])
        # A member registered at the desk with this email can now see their own data.
        Member.objects.filter(email__iexact=email, user__isnull=True).update(user=user)
    return user
