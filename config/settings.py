"""Django settings. Secrets and per-machine values come from .env (see .env.example)."""

import os
from pathlib import Path

from dotenv import load_dotenv

from .env import csv_list, database_from_url

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
ALLOWED_HOSTS = csv_list(os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1"))
# Vercel sets VERCEL_URL (this deployment) and VERCEL_PROJECT_PRODUCTION_URL (the main domain) itself.
VERCEL_HOSTS = csv_list(f'{os.environ.get("VERCEL_URL", "")},{os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "")}')
ALLOWED_HOSTS += VERCEL_HOSTS

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # needed for ExclusionConstraint (M5)
    "accounts",
    "members",
    "courts",
    "shop",
    "bar",
    "finance",
    "crm",
    "staffing",
    "notifications",
]

# Our own user model (email + role) must be set before the first migration.
AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

if os.environ.get("DATABASE_URL"):
    # Deployed (Vercel + Neon): one connection URL. Neon's "pooled" URL suits serverless functions.
    DATABASES = {"default": database_from_url(os.environ["DATABASE_URL"])}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "champions_club"),
            "USER": os.environ.get("DB_USER", "postgres"),
            "PASSWORD": os.environ.get("DB_PASSWORD", ""),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
            "OPTIONS": {"sslmode": os.environ.get("DB_SSLMODE", "prefer")},
        }
    }
# Reuse a connection between requests handled by the same process instead of reconnecting each time.
DATABASES["default"]["CONN_MAX_AGE"] = int(os.environ.get("DB_CONN_MAX_AGE", "60"))

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]

LANGUAGE_CODE = "en-in"
# Django stores datetimes in UTC (USE_TZ) and shows them in this zone.
TIME_ZONE = "Asia/Kolkata"
USE_TZ = True

LOGIN_URL = "/login/"

# Bootstrap calls the red alert "danger"; Django calls that message level "error".
MESSAGE_TAGS = {40: "danger"}

# Public facts about the club, shown on the website and in structured data. Edit for the real club.
CLUB = {
    "name": "The Champions Club",
    "phone": os.environ.get("CLUB_PHONE", "+91 98765 00000"),
    "email": os.environ.get("CLUB_EMAIL", "hello@championsclub.example"),
    "address": os.environ.get("CLUB_ADDRESS", "Sports Complex Road, Ahmedabad, Gujarat"),
    "hours": "Every day, 06:00 to 22:00",
}

# Razorpay TEST mode keys (rzp_test_...). The secret and webhook secret never reach the browser.
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")

# WhatsApp Cloud API (Meta test number + approved templates). Empty token = WhatsApp off, email only.
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v20.0")

# Email: the terminal in development. For Gmail set EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend,
# EMAIL_HOST_USER and EMAIL_HOST_PASSWORD (a Gmail "app password", never the real password).
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_USE_TLS = True
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "The Champions Club <noreply@championsclub.example>")

# Django's default ("same-origin") cuts the Google sign-in popup off from our page,
# so it can never hand back the result. This value still blocks unrelated sites.
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin-allow-popups"

# Firebase: the service-account key stays on the server; the web config values are public by design.
# Locally it's a file (path relative to the project folder). On Vercel there are no secret files,
# so the whole JSON goes into FIREBASE_CREDENTIALS_JSON instead.
FIREBASE_CREDENTIALS_PATH = BASE_DIR / os.environ.get("FIREBASE_CREDENTIALS_PATH", "firebase-service-account.json")
FIREBASE_CREDENTIALS_JSON = os.environ.get("FIREBASE_CREDENTIALS_JSON", "")
FIREBASE_WEB_API_KEY = os.environ.get("FIREBASE_WEB_API_KEY", "")
FIREBASE_AUTH_DOMAIN = os.environ.get("FIREBASE_AUTH_DOMAIN", "")
FIREBASE_PROJECT_ID = os.environ.get("FIREBASE_PROJECT_ID", "")
# Public "Web client" ID of the Firebase project; used by Google's sign-in button.
GOOGLE_OAUTH_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"  # `collectstatic` copies admin CSS/JS here for production
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Production hardening: on when DEBUG is off (the deployed site runs behind HTTPS).
CSRF_TRUSTED_ORIGINS = csv_list(os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS")) + [f"https://{h}" for h in VERCEL_HOSTS]
# Vercel Cron calls /cron/<job>/ with "Authorization: Bearer <CRON_SECRET>".
CRON_SECRET = os.environ.get("CRON_SECRET", "")
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # Vercel terminates HTTPS at its edge
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 3600
