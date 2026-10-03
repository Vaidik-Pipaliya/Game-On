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

## M6 - Cancel, front-desk booking screen, Friday social play
- `cancel_booking` locks the booking row (`select_for_update`) so a double-click cancels and refunds once. Full refund if cancelled >= 24h before the session, none inside the window. Returns the refund due; the ledger entry is written in M7. Cancelled bookings stop counting for overlap, daily limit and free hours automatically.
- **Social play reuses the overlap constraint:** `create_social_session` creates the SocialSession *and* an ordinary whole-court Booking for that hour in one transaction. So whole-court bookings and sessions can never overlap, with zero new overlap code. Creating a session on an already-booked slot fails the same way.
- Joining locks the SocialSession row, counts seats, then inserts: 20 simultaneous joins on 12 places -> exactly 12 (threaded test). A social seat counts toward the member's 2-per-day limit (PRD assumption) but never uses a free court hour; member court discount applies to the per-player price.
- Sessions: Fridays only, 1 hour, capacity >= 2; only the owner can open one.
- Screens: `/desk/book/` court grid (courts x 30-min starts, sport filter, day stepper, + links prefill the form), `/desk/book/new/` (member by phone or walk-in name + phone), day list with Cancel, `/desk/social/`.
- `grid_for_day` loads the day's bookings in one query and matches in Python: ~5 courts x 31 slots, trivial. With hundreds of courts we'd group by court in SQL.
- Shared helpers: `members.services.normalize_phone/find_member_by_phone`, `accounts.forms.BootstrapFormMixin`.

## M7 - Payments, ledger, Razorpay (test mode)
- **One ledger, one door:** only `finance/services.py` creates `Ledger` rows (`record_payment` +, `record_refund` -). Rows are append-only: `save()` on an existing row, `delete()`, and queryset `update()/delete()` all raise. Admin shows money read-only. A mistake is fixed by a reversing row, never an edit.
- **Same transaction:** a desk payment (cash/card/UPI) is written inside the booking's / membership's `transaction.atomic()`. If the booking fails (slot taken), the money row rolls back with it.
- **Refunds:** cancelling a *paid* booking 24h+ ahead writes a negative row by the original method; court revenue nets to 0. Online refunds are recorded in the ledger; the actual money is returned from the Razorpay dashboard (limitation).
- **Razorpay flow:** server creates an order (SDK) -> Checkout in the browser -> Razorpay returns payment_id + signature -> server checks `HMAC_SHA256(order_id|payment_id, key_secret)` with `hmac.compare_digest` -> records once. Only the public key id reaches the browser.
- **Webhook** `/webhooks/razorpay/`: CSRF-exempt (Razorpay can't send our token) but protected by `HMAC_SHA256(raw_body, webhook_secret)`. Returns 200 for any correctly signed event so Razorpay stops retrying.
- **Exactly once:** `mark_payment_captured` locks the Payment row and checks its status; `razorpay_payment_id` is unique. Callback + webhook + retries -> one ledger row (threaded test: 10 simultaneous captures -> 1 row). Wrong amount -> ignored.
- If Razorpay is down or keys are missing, `OnlinePaymentUnavailable` -> the booking is kept as unpaid and staff can "Take payment" later.
- Tests: 30 in finance (+ updates elsewhere): ledger rules, signatures, replays, strict-CSRF client, rollback, refunds, membership fees.

## M8 - Shop (one shelf for counter and online)
- **Safe stock:** `take_stock` is one SQL statement: `UPDATE ... SET stock = stock - q WHERE id = v AND stock >= q`. If it updates 0 rows the item is out of stock. The check and the subtraction happen together inside the database, so two tills can't both sell the last pair and stock can never go negative. Threaded test: 10 buyers (counter + online) for 1 pair -> exactly 1 order.
- `place_order` is used by both the counter and online checkout. One `transaction.atomic()`: if any line fails, earlier lines' stock is rolled back and no order/payment is saved. Lines are processed in variant-id order so two orders can't deadlock.
- Member discount comes from `price_for("shop", ...)` and is frozen on each `OrderLine`.
- Online orders take stock when placed (simplest "reserve"); cancelling returns it and refunds if paid. Pickup or delivery (flat ₹50 fee, address required). Online orders can be paid with Razorpay or at collection.
- **Low-stock alert:** fires once when a sale crosses the reorder level (not on every later sale); shown to staff and listed on the Stock page. Email to staff is added in M12.
- Session cart (`request.session["cart"]`), checkout needs Google login. Customers can only open the payment page of their own order.
- Staff (owner / shop_staff / front_desk): counter sale (formset of 5 lines), online orders (ready -> collected/delivered, cancel), stock + restock.

## M9 - Bar and cafe
- **One open tab per table is a database rule:** partial `UniqueConstraint(fields=["table"], condition=status='open')`. Two waiters opening the same table -> the second gets "Table T1 already has an open tab". Same idea: one open shift per staff member.
- **Automatic member discount:** `bill_for(tab)` calls `price_for("bar", subtotal, membership)` and shows it as its own line ("Gold member discount -10%"). Staff never press a discount button. Attaching the member later (by phone) applies it too.
- Item price is frozen on `TabLine` at order time; a menu price change doesn't alter open bills.
- **Split payment:** `settle_tab(tab, [(method, amount), ...])` - up to 3 methods, must add up to exactly the bill; one ledger row per method; the tab row is locked so two devices can't settle twice. Discount and total are frozen on the tab for reports.
- **Kitchen/bar screens:** new lines routed by `MenuItem.station`, oldest first, "Ready" button; the page reloads every 10 s (`<meta http-equiv="refresh">`) - simple auto-refresh, no websockets.
- **Shifts:** opening float + cash taken during the shift = expected cash; closing count - expected = difference. Takings are all bar ledger rows in the shift window (one till; per-staff tills would need a staff column on the ledger).
- **Day report:** takings by method (from the ledger), member discounts given, tabs closed/voided, open tabs with running totals.
- Only empty tabs can be voided; other voids would need manager approval + audit (not built).
- `config/clock.py: local_day_bounds()` is now shared by courts, bar and (next) reports. `docs/ARCHITECTURE.md` has the technology diagram.

## M10 - Owner dashboard, analytics, CSV
- **Every number is a sum of the Ledger** (`finance/reports.py`): revenue = payments + refunds (refunds are negative), split by source x method. Expenses are a separate kind and never counted as revenue. So the dashboard always equals the sum of the daily reports (test checks month total == sum of daily totals).
- Periods are club-local (Asia/Kolkata) days via `local_day_bounds`; a sale at 23:59 IST is still "today" although it's a different date in UTC (tested). Week starts Monday. Comparison is like-for-like "so far": this week Mon..today vs the same weekdays last week; this month 1st..today vs the same number of days of last month (clipped for February).
- **Amounts owed:** open bar tabs (live bill incl. discount), unpaid court bookings, unpaid online shop orders, unpaid invoices incl. GST.
- **Analytics with pandas** (`finance/analytics.py`): 30-day revenue trend (missing days filled with 0), court utilisation % = booked hours / open hours (16 h x 30 days), peak hours = sessions started per hour. Drawn with Chart.js from a `json_script` block (no inline data in JS strings).
- **CSV export** of ledger and bookings for a date range; cells starting with `= + - @` are prefixed with `'` (CSV formula-injection protection).
- `Ledger.created_at` now defaults to now (instead of auto_now_add) so history/demo rows can be dated; rows are still never edited. Added `record_expense` (used by payroll in M13). Owner-only (`role_required("owner")`).
- Scale: aggregation happens in SQL (`values().annotate(Sum)`), indexed on `created_at`; pandas only sees 30 days of rows.

## M11 - Public website, trial booking, leads
- Public pages (no login): home (club info, today's free courts, schema.org `SportsActivityLocation` JSON-LD), plans comparison table (from the `Plan` rows, so prices are never typed twice), free courts for 7 days (reuses `grid_for_day`; shows only free/taken, never names), shop (M8), trial booking, enquiry. Each page has its own title + meta description; mobile navbar collapses.
- **Every enquiry becomes a Lead** (`crm.services.create_lead`), auto-assigned to the owner/front-desk person with the fewest *open* leads, follow-up date = tomorrow, and that person is emailed **after commit** (`transaction.on_commit`), so a mail failure can never lose the enquiry.
- **Trial booking** = `book_court` (walk-in rate, pay at the club, same overlap constraint) + a `trial` lead, in one transaction: if the slot is taken, no lead is saved either.
- **Spam protection without CAPTCHA:** a hidden honeypot field (bots fill every field; we pretend success and store nothing) + rate limit of 5 submissions per IP per hour using Django's cache. Limitation: the default cache is per process; production with several processes would use a shared cache (database or Redis).
- Pipeline: new -> contacted -> quoted -> won / lost (lost needs a reason; closed leads lose their follow-up date). Overdue follow-ups are highlighted; staff can log phone/walk-in enquiries; "Register as member" opens the member form prefilled and marks the lead won with a link to the new member.
- Home page moved from `accounts` to `crm`. Club contact details are in `settings.CLUB` (env-overridable; demo values to replace).

## M12 - WhatsApp + email notifications
- **Booking saves first, message second:** `notify_booking_after_commit` uses `transaction.on_commit`, so WhatsApp/email is attempted only after the booking (and its payment) is committed. A failed or slow message never undoes a booking, and a rolled-back booking never sends a message (tested).
- WhatsApp Cloud API: POST `graph.facebook.com/<version>/<phone_number_id>/messages` with an **approved template** (`booking_confirmed`, `booking_reminder`, `booking_cancelled`; body params {{1}} court, {{2}} day, {{3}} time), bearer token from `.env`, 10 s timeout. No chatbot.
- **Consent:** WhatsApp only if the member opted in (`whatsapp_opt_in`); otherwise email. Walk-in guests get nothing (no consent recorded).
- **Every attempt is logged** in `Notification` (channel, to, status sent/failed, error, attempts). WhatsApp failure -> automatic email fallback (once). Failed messages are retried by a Retry button (`/desk/messages/`) or `manage.py retry_notifications` (cron), max 3 attempts.
- `manage.py send_booking_reminders` (cron every 15 min) reminds members of sessions in the next 2 hours, once per booking.
- Renewal reminders (M3) and low-stock alerts (M8, emailed to owner + shop staff after commit) now go through the same logged email path.
- Email: console in development; Gmail SMTP via env (`EMAIL_HOST_USER` + a Gmail **app password**). WhatsApp off when no token is set.

## M13 - Invoices (GST), employees, payroll, leave, GST summary
- **Invoices:** numbers `CC/2026/0001`, sequential per year, never reused. Two invoices created at once could pick the same next number: the unique constraint rejects one and we retry with the next (tested). GST rates limited to 0/5/12/18/28%. GST = amount x rate, rounded half up in integer paise; CGST = SGST = half (the odd paisa goes to SGST so halves always add up). Printable HTML invoice ("Print or save as PDF" in the browser, no PDF library). Marking paid writes the total incl. GST to the ledger once (row lock).
- **GST summary** per month grouped by rate: taxable, CGST, SGST, total; CSV export. Based on invoices issued. Limitation: retail/bar/court sales are treated as GST-inclusive prices and are not in this summary; filing returns is outside the system.
- **Payroll:** `run_payroll(month)` creates one row per employee (gross = monthly salary, flat 12% deduction, net). `(employee, month)` is unique, so running twice never double-pays. Paying a salary writes an **expense** row (negative) - it reduces cash but never revenue; the dashboard shows expenses separately. No PF/ESI/TDS (out of scope per PRD).
- **Leave:** 12 days a year; requests can't overlap pending/approved leave or exceed the balance; the owner approves or rejects, and approval **re-checks the balance** (another request may have been approved in between). Staff request leave at `/staff/leave/` once their login is linked to an Employee in admin.
- Owner pages share one sub-navigation: Dashboard, Invoices, GST summary, Payroll, Leave approvals.

## M14 - Demo data, docs, deploy prep
- `python manage.py seed_demo` now also creates 30 days of activity (`members/demo_activity.py`): ~850 court bookings weighted to morning/evening peaks, a few cancellations with refunds, shop counter sales, bar tabs with member discounts, new-member fees, last month's payroll (expense), two GST invoices (one paid), upcoming bookings (some unpaid -> "amounts owed"), next Friday's social session, an open tab with kitchen tickets, a pending online order, a pending leave request and an overdue lead. Seeded with `random.Random(42)` so every demo looks the same; guarded by a marker so it runs once. A test checks ledger totals equal what bookings/orders say was paid.
- Money now displays with Indian digit grouping (₹1,25,000) everywhere via the `rupees` filter.
