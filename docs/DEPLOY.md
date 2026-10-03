# Deploying to Render + Neon

The app is ready for production settings (HTTPS-only cookies, HSTS, proxy SSL header, trusted origins, SSL to Postgres) when `DJANGO_DEBUG=0`.
`python manage.py check --deploy` reports only HSTS subdomain/preload warnings, which are deliberately off on a shared `onrender.com` domain.

## Two packages still to approve

CLAUDE.md says no new dependency without asking, so these are **not added yet**:

| Package | Why it is needed |
|---|---|
| `gunicorn` | `runserver` is for development only; Render needs a production WSGI server |
| `whitenoise` | serves the Django admin's CSS/JS (from `STATIC_ROOT`) without a separate web server |

After approval: add both to `requirements.txt`, and add `"whitenoise.middleware.WhiteNoiseMiddleware"` right after `SecurityMiddleware` in `config/settings.py`.

## Steps

1. **Neon:** create a project, copy host / database / user / password. Set `DB_SSLMODE=require`.
2. **Render:** New → Web Service → connect the GitHub repo.
   - Build command: `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate`
   - Start command: `gunicorn config.wsgi`
3. **Environment variables** on Render: everything in `.env.example`, plus
   - `DJANGO_DEBUG=0`, `DJANGO_ALLOWED_HOSTS=<app>.onrender.com`, `DJANGO_CSRF_TRUSTED_ORIGINS=https://<app>.onrender.com`
   - `FIREBASE_CREDENTIALS_PATH`: upload the service-account JSON as a Render **Secret File** and point to it.
4. **First run:** Render Shell → `python manage.py seed_demo` and `python manage.py createsuperuser`.
5. **Firebase:** Authentication → Settings → Authorized domains → add `<app>.onrender.com`.
   **Google Cloud** → Credentials → Web client → Authorized JavaScript origins → add `https://<app>.onrender.com`.
6. **Razorpay** (test mode) → Webhooks → `https://<app>.onrender.com/webhooks/razorpay/`, event `payment.captured`, set a secret and put it in `RAZORPAY_WEBHOOK_SECRET`.
7. **Cron jobs** (Render Cron Jobs, same env vars): `python manage.py send_booking_reminders` (*/15 * * * *), `python manage.py retry_notifications` (*/30 * * * *), `python manage.py send_renewal_reminders` (30 0 * * * = 06:00 IST).
8. **WhatsApp** (optional): Meta developer app → WhatsApp → test number; create templates `booking_confirmed`, `booking_reminder`, `booking_cancelled`, each with body variables {{1}} court, {{2}} day, {{3}} time; set `WHATSAPP_TOKEN` and `WHATSAPP_PHONE_NUMBER_ID`.

## Backups

Neon keeps point-in-time history (restore to any moment in the retention window). For an extra copy: `pg_dump` nightly to storage. Restore drill: create a Neon branch from yesterday, point a staging service at it, run `manage.py check` and open the dashboard.
