import json

from django.conf import settings
from django.contrib.auth import login, logout
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .permissions import STAFF_ROLES, role_required
from .services import InvalidToken, get_or_create_user, verify_google_token


def home(request):
    return render(request, "accounts/home.html")


def login_page(request):
    firebase_config = {
        "apiKey": settings.FIREBASE_WEB_API_KEY,
        "authDomain": settings.FIREBASE_AUTH_DOMAIN,
        "projectId": settings.FIREBASE_PROJECT_ID,
        "googleClientId": settings.GOOGLE_OAUTH_CLIENT_ID,
    }
    return render(request, "accounts/login.html", {"firebase_config": firebase_config, "next": request.GET.get("next", "/")})


@require_POST
def firebase_login(request):
    """Steps 2-4 of the login flow: receive the Google token, verify it, start a Django session."""
    try:
        body = json.loads(request.body)
        id_token = body["id_token"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"error": "Login request was incomplete. Please try again."}, status=400)

    try:
        claims = verify_google_token(id_token)
    except InvalidToken:
        return JsonResponse({"error": "We could not verify your Google account. Please sign in again."}, status=401)

    user = get_or_create_user(claims)
    if not user.is_active:
        return JsonResponse({"error": "This account is disabled. Please contact the club."}, status=403)
    login(request, user)

    # Only follow `next` if it stays on our site (stops open-redirect tricks).
    next_url = body.get("next", "/")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        next_url = "/"
    return JsonResponse({"ok": True, "next": next_url})


@require_POST
def logout_view(request):
    logout(request)
    return redirect("home")


@role_required(*STAFF_ROLES)
def desk(request):
    # Placeholder landing page for staff; member lookup and booking screens attach here in M3+.
    return render(request, "accounts/desk.html")
