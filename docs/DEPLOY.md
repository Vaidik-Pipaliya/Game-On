# Deploying to Vercel + Neon

Vercel runs Django with **zero configuration**: it finds `manage.py`, uses `WSGI_APPLICATION = "config.wsgi.application"`, installs `requirements.txt`, runs `collectstatic` (because `STATIC_ROOT` is set) and serves static files from its CDN. The whole app becomes one serverless Vercel Function. No gunicorn or whitenoise needed, so **no new dependencies**.

What we changed for Vercel (all standard library):

| Need on Vercel | How the app handles it |
|---|---|
| One `DATABASE_URL` from the Neon integration | `config/env.py: database_from_url()`; SSL required by default |
| No secret files | `FIREBASE_CREDENTIALS_JSON` holds the whole service-account JSON (the local file still works) |
| Vercel's own domains | `VERCEL_URL` / `VERCEL_PROJECT_PRODUCTION_URL` are added to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` automatically |
| No long-running cron process | `vercel.json` schedules `GET /cron/<job>/`; the view only runs if `Authorization: Bearer <CRON_SECRET>` matches |
| HTTPS at Vercel's edge | `SECURE_PROXY_SSL_HEADER` + secure cookies + HSTS when `DJANGO_DEBUG=0` |

## Steps

1. **Database (Neon).** In Vercel: Project → Storage → add **Neon**. It creates `DATABASE_URL` (pooled, good for serverless). Neon supports the `btree_gist` extension we need.
2. **Create the tables from your laptop** (migrations aren't run during the Vercel build). In Neon, copy the *direct* (non-pooled) connection string, then:
   ```bash
   set DATABASE_URL=postgresql://...neon.tech/neondb?sslmode=require
   .venv\Scripts\python manage.py migrate
   .venv\Scripts\python manage.py seed_demo
   .venv\Scripts\python manage.py createsuperuser
   ```
   Close that terminal afterwards so your local runs use the local database again.
3. **Import the GitHub repo** in Vercel (Add New → Project → `Vaidik-Pipaliya/Game-On`). Framework preset: Django (auto-detected). Python version comes from `.python-version` (3.12).
4. **Environment variables** (Project → Settings → Environment Variables, for Production):

   | Variable | Value |
   |---|---|
   | `DJANGO_SECRET_KEY` | a long random string (also needed during the build for `collectstatic`) |
   | `DJANGO_DEBUG` | `0` |
   | `DATABASE_URL` | set by the Neon integration |
   | `FIREBASE_CREDENTIALS_JSON` | paste the **entire** service-account JSON file contents |
   | `FIREBASE_WEB_API_KEY`, `FIREBASE_AUTH_DOMAIN`, `FIREBASE_PROJECT_ID`, `GOOGLE_OAUTH_CLIENT_ID` | same as local `.env` |
   | `CRON_SECRET` | a random string; Vercel sends it to the cron endpoints |
   | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | Razorpay test keys |
   | `EMAIL_BACKEND`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` | Gmail SMTP with an app password (optional) |
   | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | optional; empty = email only |
   | `CLUB_PHONE`, `CLUB_EMAIL`, `CLUB_ADDRESS` | the real club's details |
   | `DJANGO_ALLOWED_HOSTS` | only needed for a custom domain (Vercel's domains are automatic) |

5. **Deploy.** Then allow Google sign-in on the new address (`<project>.vercel.app`):
   - Firebase console → Authentication → Settings → Authorized domains → add `<project>.vercel.app`.
   - Google Cloud → APIs & Services → Credentials → Web client → Authorized JavaScript origins → add `https://<project>.vercel.app`.
6. **Razorpay webhook** (test mode) → `https://<project>.vercel.app/webhooks/razorpay/`, event `payment.captured`, same secret as `RAZORPAY_WEBHOOK_SECRET`.
7. **Check:** open `/`, sign in, set your role to owner in `/admin/`, open `/owner/dashboard/`. Test a cron job by hand:
   ```bash
   curl -H "Authorization: Bearer <CRON_SECRET>" https://<project>.vercel.app/cron/retry-notifications/
   ```

## Scheduled jobs (`vercel.json`)

| Job | Schedule (UTC) | IST |
|---|---|---|
| `/cron/renewal-reminders/` | `30 0 * * *` | 06:00 |
| `/cron/booking-reminders/?window_hours=17` | `30 0 * * *` | 06:00, reminds every session today |
| `/cron/retry-notifications/` | `30 6 * * *` | 12:00 |

The **Hobby (free) plan allows each cron job once a day** (and only to within the hour), so booking reminders go out in one morning batch instead of 2 hours before each session. On **Pro**, change the reminder job to `*/15 * * * *` without `window_hours` (default 2 hours) and retries to `*/30 * * * *`.

## Things to know about serverless

- Each request runs in a function instance; instances come and go. The enquiry rate limit uses Django's in-memory cache, so it is per instance (weaker than on one server). A shared cache (e.g. Upstash Redis) would fix it.
- Messages are sent inside the request after commit, so a slow WhatsApp call makes that one response slower (10 s timeout). Vercel Queues would move this to the background.
- Everything else (constraints, row locks, the ledger) lives in PostgreSQL, so serverless scaling doesn't weaken any guarantee: many instances still can't double-book.

## Backups

Neon keeps point-in-time history: restore to any moment inside the retention window, or create a branch from a past time to inspect it. Restore drill: branch the database from yesterday, point a Vercel preview deployment's `DATABASE_URL` at the branch, open the dashboard.
