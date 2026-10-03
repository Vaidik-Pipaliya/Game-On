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
- Login uses Google's "Sign in with Google" button (GIS), then `signInWithCredential` in Firebase, then our server verifies the Firebase token. We moved off Firebase's popup helper because it needs HTTPS and Chrome partitions its storage.
- Django's default `Cross-Origin-Opener-Policy: same-origin` broke the Google popup (blank window / popup-closed-by-user). We set `SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin-allow-popups"`.

## M3 - Members, plans, expiry, lookup
- Rules live in `members/services.py` (not views): `register_member` creates Member + Membership in one `transaction.atomic()`; a Junior needs age < 18 and an adult guardian, and a minor can only take the Junior plan.
- Membership status (active/expiring/expired/cancelled) is computed by `Membership.status_on(day)`, never stored. Expiring = 14 days or fewer left.
- Renewal extends from the old end date if still valid (no paid days lost), otherwise starts today.
- `send_renewal_reminders` emails at 14/7/1 days; `reminder_sent_on` and a "newer membership exists" check prevent duplicates and reminders to people who already renewed. Run `manage.py send_renewal_reminders` daily (cron on Render).
- Search is `icontains` on name/phone, limited to 20 rows: fine for thousands of members; at 100k add an index/trigram search.
- Form validates formats (10-digit phone, +91 stripped, DOB not in future); the service validates business rules. The money filter `rupees` turns paise into rupees for display.

## M4 - One pricing function
- `members/pricing.py: price_for(item_type, base_paise, membership, free_hours_left=0)` returns `Price(amount_paise, kind, discount_pct)`; courts, shop and bar all call it, so a discount rule exists in exactly one place.
- Only active or expiring memberships get benefits; expired or cancelled fall back to walk-in automatically (no job needed).
- Pure function: free-hours-left is passed in by the booking code (M5), so it needs no database and tests are instant.
- Integer maths with round-half-up (`+50`), never floats. `kind` ("free"/"member"/"walk_in") and `discount_pct` feed the visible "Gold member discount -10%" bill line in M9.
- The price is stored on the Booking/Order line when made, so a later plan change can't rewrite history (tested in M5).

## M5 - Court booking engine (core depth)
- **No double booking is enforced by PostgreSQL**, not Python: `ExclusionConstraint` on `TSTZRANGE(start, end)` + `court` with `&&` (overlaps) and `=`, only for `status='confirmed' AND kind='exclusive'`. Needs the `btree_gist` extension (migration `courts.0002`). Ranges are half-open `[start, end)` so 18:00-19:00 and 19:00-20:00 don't clash; 18:00 and 18:30 do.
- A `CheckConstraint` also forces whole-court bookings to be exactly 1 hour.
- **Max 2 per day:** `book_court` runs in `transaction.atomic()` and locks the member row with `select_for_update()`. A second request for the same member waits for the first to commit, so both can't read "1 booking" and both pass. Only `confirmed` bookings count, on the club-local day, so cancelling returns quota. Walk-in guests (no Member row) are not limited.
- Overlap -> the DB raises IntegrityError -> we check the constraint name and raise a friendly `SlotTaken` ("Court 1 is taken at 18:30. Pick another court or time.").
- Price comes from `price_for` and is stored on the booking. Free hours left this month = plan allowance minus confirmed bookings priced 0 (so cancelling gives the free hour back). Member rate depends on the *session date*, not the booking date.
- Slots: start on :00/:30, club hours 06:00-22:00 (assumption, constants in `courts/services.py`), not in the past.
- Tests: 26, including `ConcurrencyTests` with real threads and real connections: 50 simultaneous requests for one slot -> exactly 1 booking; 1 member racing for 5 slots -> exactly 2.
- Scaling: the constraint is backed by a GiST index, so overlap checks stay fast as bookings grow; the daily-limit count uses one indexed range query per request.
- Not yet: cancel/refund (M6/M7), Friday social play (M6: it must also block exclusive bookings), booking screens (M6).
