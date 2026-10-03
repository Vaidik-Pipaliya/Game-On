# Project notes

## M1 - Project, models, admin, seed data
- Django project `config` with 9 small apps; one `AUTH_USER_MODEL` (`accounts.User`) with a `role` field, set before the first migration because it can't be swapped later.
- Money is stored as integer paise, times as UTC (`USE_TZ=True`), shown in Asia/Kolkata.
- Plan benefits (court discount %, shop %, bar %) live in the `Plan` table, so the owner edits them in admin. Court walk-in rate lives on `Court`; member price = walk-in rate minus plan discount (done in the pricing function, M4).
- `Membership` status is computed from `end_date`, not stored, so it can never be out of date.
- `courts/migrations/0002_btree_gist.py` enables the Postgres extension needed for the M5 exclusion constraint.
- `python manage.py seed_demo` is idempotent (safe to run twice). It lives in the `members` app instead of a separate seed app to avoid an extra app.

## M2 - Google login (Firebase) and roles
- Flow: browser Google popup -> Firebase ID token -> POST /auth/firebase/ -> `firebase_admin.auth.verify_id_token` -> `login()` -> normal Django session. Passwords are never stored (`set_unusable_password`).
- A forged or expired token gets 401; `next` redirects are only followed if they stay on our host.
- New users are `member`. The owner promotes staff by changing `role` in admin. A member registered at the desk with the same email is linked to the new user on first login.
- `role_required(*roles)` in `accounts/permissions.py` checks the role on the server for every request (403 page for wrong role, redirect to login if anonymous).
- Tests mock the Firebase call, so they run without internet or keys.
