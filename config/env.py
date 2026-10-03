"""Small helpers for reading deployment settings from environment variables (standard library only)."""

from urllib.parse import parse_qs, unquote, urlparse


def database_from_url(url):
    """postgres://user:pass@host:5432/db?sslmode=require -> Django DATABASES["default"].

    Vercel's Neon integration gives one DATABASE_URL instead of separate DB_* variables.
    """
    parts = urlparse(url)
    options = {key: values[-1] for key, values in parse_qs(parts.query).items()}
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parts.path.lstrip("/"),
        "USER": unquote(parts.username or ""),
        "PASSWORD": unquote(parts.password or ""),
        "HOST": parts.hostname or "",
        "PORT": str(parts.port or 5432),
        "OPTIONS": {"sslmode": options.get("sslmode", "require")},
    }


def csv_list(value):
    """'a.com, b.com,' -> ['a.com', 'b.com']"""
    return [item.strip() for item in (value or "").split(",") if item.strip()]
